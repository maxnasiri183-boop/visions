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
    if (args.length != 4)
      throw new IllegalArgumentException("Usage: CLIP SOURCE_ID FRAME_INDEX SPEED");
    Path clip = Path.of(args[0]);
    String source = args[1];
    if (!source.matches("[A-Za-z0-9_-]{1,128}"))
      throw new IllegalArgumentException("Invalid source_id");
    int selected = Integer.parseInt(args[2]);
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
      String manifest =
          "frames/" + source + "/" + asset + "/" + VERSION + "/v0/" + selected + ".json";
      feed.play(
          speed,
          selected,
          (image, pts, ingested) -> {
            byte[] saved;
            try {
              saved = read(s3, manifest);
            } catch (S3Exception e) {
              if (e.statusCode() != 404) throw e;
              var events =
                  makeEvents(s3, detector, image, source, asset, selected, pts, ingested, feed);
              byte[] proposal = JSON.writeValueAsBytes(events);
              try {
                s3.putObject(
                    PutObjectRequest.builder()
                        .bucket(BUCKET)
                        .key(manifest)
                        .contentType("application/json")
                        .ifNoneMatch("*")
                        .build(),
                    RequestBody.fromBytes(proposal));
                saved = proposal;
              } catch (S3Exception conflict) {
                if (conflict.statusCode() != 412 && conflict.statusCode() != 409) throw conflict;
                saved = read(s3, manifest);
              }
            }
            for (var event : JSON.readTree(saved)) {
              String id = event.required("detection_id").asText();
              var ack =
                  kafka.send(new ProducerRecord<>(TOPIC, id, JSON.writeValueAsString(event))).get();
              System.out.printf(
                  "detection_id=%s topic=%s partition=%d offset=%d%n",
                  id, ack.topic(), ack.partition(), ack.offset());
            }
            System.out.println("manifest=s3://" + BUCKET + "/" + manifest);
          });
    }
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
      FileFeed feed)
      throws Exception {
    var events = new ArrayList<Detection>();
    for (var face : detector.detect(image)) {
      var box = face.box();
      String id = UUID.randomUUID().toString();
      String key = "crops/" + id + ".png";
      var buffer = new ByteArrayOutputStream();
      ImageIO.write(image.getSubimage(box.x, box.y, box.width, box.height), "png", buffer);
      byte[] png = buffer.toByteArray();
      s3.putObject(
          r -> r.bucket(BUCKET).key(key).contentType("image/png").ifNoneMatch("*"),
          RequestBody.fromBytes(png));
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
