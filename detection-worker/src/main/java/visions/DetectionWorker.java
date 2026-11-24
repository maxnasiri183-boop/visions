package visions;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.*;
import java.net.URI;
import java.nio.file.*;
import java.security.*;
import java.util.*;
import org.apache.kafka.clients.producer.*;
import org.apache.kafka.common.serialization.StringSerializer;
import software.amazon.awssdk.auth.credentials.EnvironmentVariableCredentialsProvider;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.s3.S3Client;

public final class DetectionWorker {
  private static final String BUCKET = "visions-private";
  private static final String TOPIC = "face.detected.v1";
  private static final String MODEL_HASH =
      "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4";
  private static final ObjectMapper JSON = new ObjectMapper();

  public static void main(String[] args) throws Exception {
    boolean sustained = args.length == 5 && args[2].equals("all");
    if (!sustained && args.length != 4)
      throw new IllegalArgumentException("Usage: CLIP SOURCE_ID FRAME_INDEX SPEED or CLIP SOURCE_ID all SPEED LOOPS");
    Path clip = Path.of(args[0]);
    String source = args[1];
    if (!source.matches("[A-Za-z0-9_-]{1,128}"))
      throw new IllegalArgumentException("Invalid source_id");
    int selected = sustained ? -1 : Integer.parseInt(args[2]);
    int loops = sustained ? Integer.parseInt(args[4]) : 1;
    if ((!sustained && selected < 0) || loops < 1) throw new IllegalArgumentException("Invalid frame range");
    double speed = Double.parseDouble(args[3]);
    String model = System.getenv("MODEL_PATH");
    if (!MODEL_HASH.equals(hash(Path.of(model)))) throw new IOException("Wrong YuNet model digest");
    String asset = hash(clip);
    var feed = new FileFeed(clip);
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
      if (sustained) {
        feed.play(speed, -1, 1, frame ->
            processor.process(feed, frame, source + "-warmup", asset, true));
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
      feed.play(speed, selected, loops, frame ->
          processor.process(feed, frame, source, asset, false));
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
