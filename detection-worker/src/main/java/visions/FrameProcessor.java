package visions;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;
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

  private record Published(JsonNode event, String topic, int partition, long offset) {}
  @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
  private record FrameEvent(int schemaVersion, String sourceId, String assetSha256,
      String detectorVersion, int videoTrack, int frameIndex, long pts, List<Integer> timeBase,
      List<Integer> frameSize, String frameIngestedAt, String manifestKey, List<Published> detections) {}

  void process(FileFeed feed, FileFeed.Frame frame, String source, String asset, boolean warmup, boolean emitFrameEvent)
      throws Exception {
    if (emitFrameEvent) {
      System.out.println("frame_received=" + json.writeValueAsString(java.util.Map.of(
          "source_id", source, "frame_index", frame.index(), "pts", frame.pts(),
          "ingested_at", frame.ingested().toString(), "lag_ms", frame.lagMs())));
    }
    var stats = new LinkedHashMap<String, Object>();
    stats.put("source", source);
    stats.put("index", frame.index());
    stats.put("warmup", warmup);
    stats.put("ingested_at", frame.ingested().toString());
    stats.put("media_pts_s", (double) frame.pts() * feed.timeBase.get(0) / feed.timeBase.get(1));
    stats.put("decode_ms", frame.decodeMs());
    stats.put("lag_ms", frame.lagMs());
    stats.put("ran_inference", false);
    var saved = observations.resolveFrame(source, asset, VERSION, frame.index(), frame.ingested(),
        json, () -> detect(feed, frame, source, asset, stats));
    stats.put("manifest_io_ms", saved.ioMs());
    var events = saved.events();
    long start = System.nanoTime();
    var published = new ArrayList<Published>(events.size());
    for (var event : events) {
      var ack = publisher.publish(event);
      if (emitFrameEvent) published.add(new Published(event, ack.topic(), ack.partition(), ack.offset()));
    }
    stats.put("kafka_publish_ms", events.isEmpty() ? 0 : (System.nanoTime() - start) / 1e6);
    stats.put("detections", events.size());
    stats.put("wall_time", Instant.now().toEpochMilli() / 1000.0);
    System.out.println("manifest=" + saved.uri());
    System.out.println("frame_stats=" + json.writeValueAsString(stats));
    if (emitFrameEvent) {
      var complete = new FrameEvent(1, source, asset, VERSION, 0, frame.index(), frame.pts(),
          feed.timeBase, List.of(feed.width, feed.height), saved.ingestedAt(), saved.key(), published);
      System.out.println("frame_event=" + json.writeValueAsString(complete));
    }
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
