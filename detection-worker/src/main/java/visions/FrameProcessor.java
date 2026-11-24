package visions;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

final class FrameProcessor {
  static final String VERSION = "yunet-2023mar-ort640-bgr-v1";

  private final YuNet detector;
  private final CropStore crops;
  private final ObservationStore observations;
  private final EventPublisher publisher;
  private final ObjectMapper json;

  FrameProcessor(YuNet detector, CropStore crops, ObservationStore observations,
      EventPublisher publisher, ObjectMapper json) {
    this.detector = detector;
    this.crops = crops;
    this.observations = observations;
    this.publisher = publisher;
    this.json = json;
  }

  void process(FileFeed feed, FileFeed.Frame frame, String source, String asset, boolean warmup)
      throws Exception {
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
    var saved = observations.resolve(manifest, () ->
        json.writeValueAsBytes(detect(feed, frame, source, asset, stats)));
    stats.put("manifest_io_ms", saved.ioMs());
    var events = json.readTree(saved.bytes());
    long start = System.nanoTime();
    for (var event : events) publisher.publish(event);
    stats.put("kafka_publish_ms", events.isEmpty() ? 0 : (System.nanoTime() - start) / 1e6);
    stats.put("detections", events.size());
    stats.put("wall_time", Instant.now().toEpochMilli() / 1000.0);
    System.out.println("manifest=s3://" + observations.bucket + "/" + manifest);
    System.out.println("frame_stats=" + json.writeValueAsString(stats));
  }

  private List<Detection> detect(FileFeed feed, FileFeed.Frame frame, String source,
      String asset, Map<String, Object> stats) throws Exception {
    var events = new ArrayList<Detection>();
    var faces = detector.detect(frame.image());
    stats.put("ran_inference", true);
    stats.put("preprocess_ms", detector.preprocessMs());
    stats.put("inference_ms", detector.inferenceMs());
    double uploadMs = 0;
    for (var face : faces) {
      var box = face.box();
      String id = UUID.randomUUID().toString();
      var crop = crops.save(id, frame.image(), box);
      uploadMs += crop.uploadMs();
      events.add(new Detection(1, id, source, asset, 0, frame.index(), frame.pts(),
          feed.timeBase, List.of(feed.width, feed.height), frame.ingested().toString(), VERSION,
          List.of(box.x, box.y, box.width, box.height), face.points(), face.score(), crop.uri(), crop.sha256()));
    }
    stats.put("crop_upload_ms", uploadMs);
    return events;
  }
}
