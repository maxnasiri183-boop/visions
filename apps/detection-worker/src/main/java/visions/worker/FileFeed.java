package visions.worker;

import com.fasterxml.jackson.databind.*;
import java.awt.image.*;
import java.io.*;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.concurrent.TimeUnit;

// Bounded, seekable acceptance clips. ffprobe and ffmpeg both select the first video stream.
final class FileFeed {
  interface Receiver {
    void accept(Frame frame) throws Exception;
  }

  record Frame(BufferedImage image, int index, long pts, Instant ingested, double decodeMs, double lagMs) {}

  final int width, height;
  final List<Integer> timeBase;
  final JsonNode frames;
  private final Path clip;

  FileFeed(Path clip) throws Exception {
    this.clip = clip;
    var probe =
        new ProcessBuilder(
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,time_base:stream_side_data=rotation:frame=best_effort_timestamp",
                "-of",
                "json",
                clip.toString())
            .redirectError(ProcessBuilder.Redirect.INHERIT)
            .start();
    JsonNode metadata;
    try (var input = probe.getInputStream()) {
      metadata = new ObjectMapper().readTree(input);
    }
    if (probe.waitFor() != 0) throw new IOException("ffprobe failed");
    var stream = metadata.required("streams").required(0);
    if (stream.path("side_data_list").findValues("rotation").stream().anyMatch(v -> v.asInt() != 0))
      throw new IllegalArgumentException("Normalize rotated input before the acceptance run");
    width = stream.required("width").asInt();
    height = stream.required("height").asInt();
    String[] tb = stream.required("time_base").asText().split("/");
    timeBase = List.of(Integer.parseInt(tb[0]), Integer.parseInt(tb[1]));
    frames = metadata.required("frames");
  }

  void play(double speed, int selected, int loops, Receiver receiver) throws Exception {
    playFrom(speed, selected, loops, 0, null, receiver);
  }

  void playFrom(double speed, int selected, int loops, int firstFrame, Instant scheduledAt, Receiver receiver) throws Exception {
    if (!Double.isFinite(speed) || speed <= 0 || loops < 1 || selected < -1 || selected >= frames.size()
        || firstFrame < 0 || firstFrame > frames.size())
      throw new IllegalArgumentException(
          "Expected positive finite speed and an existing frame index");
    if (firstFrame == frames.size()) return;
    var decoder =
        new ProcessBuilder(
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-threads",
                "1",
                "-stream_loop",
                Integer.toString(loops - 1),
                "-noautorotate",
                "-i",
                clip.toString(),
                "-map",
                "0:v:0",
                "-fps_mode",
                "passthrough",
                "-threads",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "bgr24",
                "pipe:1")
            .redirectError(ProcessBuilder.Redirect.INHERIT)
            .start();
    var image = new BufferedImage(width, height, BufferedImage.TYPE_3BYTE_BGR);
    byte[] pixels = ((DataBufferByte) image.getRaster().getDataBuffer()).getData();
    long firstPts = pts(scheduledAt == null ? firstFrame : 0);
    long started = System.nanoTime();
    long clockStart = scheduledAt == null ? started
        : started + Duration.between(Instant.now(), scheduledAt).toNanos();
    int framesPerLoop = frames.size();
    // Sustained mode uses the pinned constant-rate fixture.
    long period = loops == 1 ? 0 : pts(framesPerLoop - 1) - firstPts + pts(1) - firstPts;
    try (var input = new DataInputStream(new BufferedInputStream(decoder.getInputStream()))) {
      for (int i = 0; i < Math.multiplyExact(framesPerLoop, loops); i++) {
        long readStarted = System.nanoTime();
        input.readFully(pixels);
        double readMs = (System.nanoTime() - readStarted) / 1e6;
        if (i < firstFrame) continue;
        if (scheduledAt == null && firstFrame > 0 && i == firstFrame) clockStart = System.nanoTime();
        long timestamp = pts(i % framesPerLoop) + (i / framesPerLoop) * period;
        long due =
            clockStart
                + (long) ((timestamp - firstPts) * 1e9 * timeBase.get(0) / timeBase.get(1) / speed);
        TimeUnit.NANOSECONDS.sleep(Math.max(0, due - System.nanoTime()));
        if (selected == -1 || i == selected)
          receiver.accept(new Frame(image, i, timestamp, Instant.now(), readMs,
              Math.max(0, System.nanoTime() - due) / 1e6));
      }
      if (input.read() != -1 || decoder.waitFor() != 0)
        throw new IOException("Frame count/decode mismatch");
    } finally {
      decoder.destroyForcibly();
    }
    System.out.printf(
        "decoded_frames=%d selected_frame=%d speed=%.3f elapsed_s=%.3f%n",
        framesPerLoop * loops, selected, speed, (System.nanoTime() - started) / 1e9);
  }

  private long pts(int index) {
    return frames.get(index).required("best_effort_timestamp").longValue();
  }
}
