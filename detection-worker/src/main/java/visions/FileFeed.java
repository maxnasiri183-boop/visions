package visions;

import com.fasterxml.jackson.databind.*;
import java.awt.image.*;
import java.io.*;
import java.nio.file.Path;
import java.time.Instant;
import java.util.List;
import java.util.concurrent.TimeUnit;

// Bounded, seekable acceptance clips. ffprobe and ffmpeg both select the first video stream.
final class FileFeed {
  interface Receiver {
    void accept(BufferedImage image, long pts, Instant ingested) throws Exception;
  }

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

  void play(double speed, int selected, Receiver receiver) throws Exception {
    if (!Double.isFinite(speed) || speed <= 0 || selected < 0 || selected >= frames.size())
      throw new IllegalArgumentException(
          "Expected positive finite speed and an existing frame index");
    var decoder =
        new ProcessBuilder(
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-threads",
                "1",
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
    long firstPts = pts(0), start = System.nanoTime();
    try (var input = new DataInputStream(new BufferedInputStream(decoder.getInputStream()))) {
      for (int i = 0; i < frames.size(); i++) {
        input.readFully(pixels);
        long timestamp = pts(i);
        long due =
            start
                + (long) ((timestamp - firstPts) * 1e9 * timeBase.get(0) / timeBase.get(1) / speed);
        TimeUnit.NANOSECONDS.sleep(Math.max(0, due - System.nanoTime()));
        if (i == selected) receiver.accept(image, timestamp, Instant.now());
      }
      if (input.read() != -1 || decoder.waitFor() != 0)
        throw new IOException("Frame count/decode mismatch");
    } finally {
      decoder.destroyForcibly();
    }
    System.out.printf(
        "decoded_frames=%d selected_frame=%d speed=%.3f elapsed_s=%.3f%n",
        frames.size(), selected, speed, (System.nanoTime() - start) / 1e9);
  }

  private long pts(int index) {
    return frames.get(index).required("best_effort_timestamp").longValue();
  }
}
