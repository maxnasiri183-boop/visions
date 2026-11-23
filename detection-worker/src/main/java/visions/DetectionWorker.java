package visions;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.awt.image.BufferedImage;
import java.io.*;
import java.net.URI;
import java.nio.file.*;
import java.security.*;
import java.time.Instant;
import java.util.*;
import javax.imageio.ImageIO;
import org.apache.kafka.clients.producer.*;
import org.apache.kafka.common.serialization.StringSerializer;
import software.amazon.awssdk.auth.credentials.EnvironmentVariableCredentialsProvider;
import software.amazon.awssdk.core.sync.RequestBody;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.*;

public final class DetectionWorker {
  private static final String BUCKET = "visions-private";
  private static final String TOPIC = "face.detected.v1";
  private static final String MODEL_HASH =
      "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4";
  private static final String VERSION = "yunet-2023mar-ort640-bgr-v1";
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
      if (sustained) {
        feed.play(speed, -1, 1, frame ->
            process(s3, kafka, detector, feed, frame, source + "-warmup", asset, true));
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
          process(s3, kafka, detector, feed, frame, source, asset, false));
    }
  }

  private static void process(S3Client s3, KafkaProducer<String, String> kafka, YuNet detector,
      FileFeed feed, FileFeed.Frame frame, String source, String asset, boolean warmup) throws Exception {
    String manifest = "frames/" + source + "/" + asset + "/" + VERSION + "/v0/" + frame.index() + ".json";
    var stats = new LinkedHashMap<String, Object>();
    stats.put("source", source);
    stats.put("index", frame.index());
    stats.put("warmup", warmup);
    stats.put("ingested_at", frame.ingested().toString());
    stats.put("media_pts_s", (double) frame.pts() * feed.timeBase.get(0) / feed.timeBase.get(1));
    stats.put("decode_ms", frame.decodeMs());
    stats.put("lag_ms", frame.lagMs());
    stats.put("ran_inference", false);
    double manifestMs = 0;
    long manifestStart = System.nanoTime();
    byte[] saved;
    try {
      saved = read(s3, manifest);
    } catch (S3Exception e) {
      if (e.statusCode() != 404) throw e;
      manifestMs = (System.nanoTime() - manifestStart) / 1e6;
      var events = makeEvents(s3, detector, frame.image(), source, asset, frame.index(), frame.pts(),
          frame.ingested(), feed, stats);
      byte[] proposal = JSON.writeValueAsBytes(events);
      manifestStart = System.nanoTime();
      try {
        s3.putObject(PutObjectRequest.builder().bucket(BUCKET).key(manifest)
            .contentType("application/json").ifNoneMatch("*").build(), RequestBody.fromBytes(proposal));
        saved = proposal;
      } catch (S3Exception conflict) {
        if (conflict.statusCode() != 412 && conflict.statusCode() != 409) throw conflict;
        saved = read(s3, manifest);
      }
    }
    stats.put("manifest_io_ms", manifestMs + (System.nanoTime() - manifestStart) / 1e6);
    var events = JSON.readTree(saved);
    long start = System.nanoTime();
    for (var event : events) {
      String id = event.required("detection_id").asText();
      var ack = kafka.send(new ProducerRecord<>(TOPIC, id, JSON.writeValueAsString(event))).get();
      System.out.printf("detection_id=%s topic=%s partition=%d offset=%d%n", id, ack.topic(), ack.partition(), ack.offset());
    }
    stats.put("kafka_publish_ms", events.isEmpty() ? 0 : (System.nanoTime() - start) / 1e6);
    stats.put("detections", events.size());
    stats.put("wall_time", Instant.now().toEpochMilli() / 1000.0);
    System.out.println("manifest=s3://" + BUCKET + "/" + manifest);
    System.out.println("frame_stats=" + JSON.writeValueAsString(stats));
  }

  private static byte[] read(S3Client s3, String key) {
    return s3.getObjectAsBytes(r -> r.bucket(BUCKET).key(key)).asByteArray();
  }

  private static List<Detection> makeEvents(
      S3Client s3,
      YuNet detector,
      BufferedImage image,
      String source,
      String asset,
      int index,
      long pts,
      Instant ingested,
      FileFeed feed, Map<String, Object> stats)
      throws Exception {
    var events = new ArrayList<Detection>();
    var faces = detector.detect(image);
    stats.put("ran_inference", true);
    stats.put("preprocess_ms", detector.preprocessMs());
    stats.put("inference_ms", detector.inferenceMs());
    double uploadMs = 0;
    for (var face : faces) {
      var box = face.box();
      String id = UUID.randomUUID().toString();
      String key = "crops/" + id + ".png";
      var buffer = new ByteArrayOutputStream();
      ImageIO.write(image.getSubimage(box.x, box.y, box.width, box.height), "png", buffer);
      byte[] png = buffer.toByteArray();
      long start = System.nanoTime();
      s3.putObject(
          r -> r.bucket(BUCKET).key(key).contentType("image/png").ifNoneMatch("*"),
          RequestBody.fromBytes(png));
      uploadMs += (System.nanoTime() - start) / 1e6;
      events.add(
          new Detection(
              1,
              id,
              source,
              asset,
              0,
              index,
              pts,
              feed.timeBase,
              List.of(feed.width, feed.height),
              ingested.toString(),
              VERSION,
              List.of(box.x, box.y, box.width, box.height),
              face.points(),
              face.score(),
              "s3://" + BUCKET + "/" + key,
              HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(png))));
    }
    stats.put("crop_upload_ms", uploadMs);
    return events;
  }

  private static String hash(Path path) throws Exception {
    var digest = MessageDigest.getInstance("SHA-256");
    try (var input = new DigestInputStream(Files.newInputStream(path), digest)) {
      input.transferTo(OutputStream.nullOutputStream());
    }
    return HexFormat.of().formatHex(digest.digest());
  }
}
