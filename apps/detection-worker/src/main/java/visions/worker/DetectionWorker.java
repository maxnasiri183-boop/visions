package visions.worker;

import visions.YuNet;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.DigestInputStream;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.Map;
import java.util.Properties;
import org.apache.kafka.clients.producer.KafkaProducer;
import org.apache.kafka.common.serialization.StringSerializer;
import software.amazon.awssdk.auth.credentials.EnvironmentVariableCredentialsProvider;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.s3.S3Client;

public final class DetectionWorker {
  private static final String BUCKET = System.getenv().getOrDefault("DETECTION_BUCKET", "visions-private");
  private static final String TOPIC = System.getenv().getOrDefault("DETECTION_TOPIC", "face.detected.v1");
  private static final String MODEL_HASH =
      "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4";
  private static final ObjectMapper JSON = new ObjectMapper();

  public static void main(String[] args) throws Exception {
    boolean serve = args.length == 1 && args[0].equals("serve");
    ClipTask single = serve ? null : ClipTask.arguments(args);
    String model = System.getenv("MODEL_PATH");
    if (!MODEL_HASH.equals(hash(Path.of(model)))) throw new IOException("Wrong YuNet model digest");
    var props = new Properties();
    props.put("bootstrap.servers", System.getenv("KAFKA_BROKERS"));
    props.put("acks", "all");
    props.put("enable.idempotence", "true");
    props.put("key.serializer", StringSerializer.class.getName());
    props.put("value.serializer", StringSerializer.class.getName());
    try (var detector = new YuNet(model);
        var s3 =
            S3Client.builder()
                .region(Region.US_EAST_1)
                .forcePathStyle(true)
                .credentialsProvider(EnvironmentVariableCredentialsProvider.create())
                .endpointOverride(URI.create(System.getenv("S3_ENDPOINT")))
                .build();
        var kafka = new KafkaProducer<String, String>(props)) {
      var processor = new FrameProcessor(detector, new CropStore(s3, BUCKET),
          new ObservationStore(s3, BUCKET), new EventPublisher(kafka, TOPIC, JSON), JSON);
      if (serve) {
        System.out.println("worker_ready");
        try (var commands = new BufferedReader(new InputStreamReader(System.in, java.nio.charset.StandardCharsets.UTF_8))) {
          String line;
          while ((line = commands.readLine()) != null) {
            var request = JSON.readTree(line);
            if (!request.path("request_id").isTextual()) throw new IllegalArgumentException("Missing request_id");
            play(ClipTask.request(request), processor, request.required("request_id").asText());
          }
        }
      } else {
        play(single, processor, "standalone");
      }
    }
  }

  private static void play(ClipTask task, FrameProcessor processor, String requestId) throws Exception {
    String asset = hash(task.clip());
    var feed = new FileFeed(task.clip());
    if (task.sustained()) {
      feed.play(task.speed(), -1, 1, frame ->
          processor.process(feed, frame, task.source() + "-warmup", asset, true, false));
      System.out.println("warmup_complete");
      String barrier = System.getenv("BENCH_START_FILE");
      if (barrier != null) {
        long deadline = System.nanoTime() + 180_000_000_000L;
        while (!Files.exists(Path.of(barrier))) {
          if (System.nanoTime() > deadline) throw new IOException("Benchmark peers failed to warm up");
          Thread.sleep(20);
        }
      }
    }
    feed.playFrom(task.speed(), task.selected(), task.loops(), task.firstFrame(), task.scheduledAt(), frame ->
        processor.process(feed, frame, task.source(), asset, false, task.continuous()));
    if (task.continuous()) {
      System.out.println("clip_done=" + JSON.writeValueAsString(Map.of(
          "request_id", requestId, "source_id", task.source(), "frame_count", feed.frames.size())));
    }
  }

  private static String hash(Path path) throws Exception {
    var digest = MessageDigest.getInstance("SHA-256");
    try (var input = new DigestInputStream(Files.newInputStream(path), digest)) {
      input.transferTo(OutputStream.nullOutputStream());
    }
    return HexFormat.of().formatHex(digest.digest());
  }
}
