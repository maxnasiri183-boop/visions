package visions.worker;

import com.fasterxml.jackson.databind.JsonNode;
import java.nio.file.Path;
import java.time.Instant;
import java.math.BigDecimal;

record ClipTask(Path clip, String source, double speed, int selected, int loops, int firstFrame,
    boolean sustained, boolean continuous, Instant scheduledAt) {
  ClipTask {
    if (!source.matches("[A-Za-z0-9_-]{1,128}"))
      throw new IllegalArgumentException("Invalid source_id");
    if (!Double.isFinite(speed) || speed <= 0 || firstFrame < 0 || loops < 1)
      throw new IllegalArgumentException("Invalid speed or frame range");
  }

  static ClipTask arguments(String[] args) {
    if (args.length != 4 && args.length != 5)
      throw new IllegalArgumentException("Usage: CLIP SOURCE_ID FRAME_INDEX SPEED, CLIP SOURCE_ID all SPEED LOOPS, CLIP SOURCE_ID continuous SPEED START_INDEX, or serve");
    var clip = Path.of(args[0]);
    double speed = Double.parseDouble(args[3]);
    if (args.length == 5 && args[2].equals("all"))
      return new ClipTask(clip, args[1], speed, -1, Integer.parseInt(args[4]), 0, true, false, null);
    if (args.length == 5 && args[2].equals("continuous"))
      return new ClipTask(clip, args[1], speed, -1, 1, Integer.parseInt(args[4]), false, true, null);
    if (args.length != 4) throw new IllegalArgumentException("Invalid clip mode");
    int frame = Integer.parseInt(args[2]);
    if (frame < 0) throw new IllegalArgumentException("Invalid selected frame");
    return new ClipTask(clip, args[1], speed, frame, 1, 0, false, false, null);
  }

  static ClipTask request(JsonNode row) {
    if (!row.path("clip").isTextual() || !row.path("source_id").isTextual()
        || !row.path("speed").isNumber() || !row.path("start_index").isIntegralNumber()
        || !row.path("start_index").canConvertToInt())
      throw new IllegalArgumentException("Expected clip, source_id, numeric speed and integer start_index");
    Instant scheduledAt = null;
    if (row.hasNonNull("scheduled_at")) {
      var schedule = row.get("scheduled_at");
      if (!schedule.isNumber() || !Double.isFinite(schedule.asDouble()))
        throw new IllegalArgumentException("scheduled_at must be Unix epoch seconds");
      BigDecimal[] epoch = schedule.decimalValue().divideAndRemainder(BigDecimal.ONE);
      scheduledAt = Instant.ofEpochSecond(epoch[0].longValueExact(), epoch[1].movePointRight(9).longValue());
    }
    return new ClipTask(Path.of(row.get("clip").asText()), row.get("source_id").asText(),
        row.get("speed").asDouble(), -1, 1, row.get("start_index").asInt(), false, true, scheduledAt);
  }
}
