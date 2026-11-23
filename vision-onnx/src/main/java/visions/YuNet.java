package visions;

import ai.onnxruntime.*;
import java.awt.*;
import java.awt.image.BufferedImage;
import java.nio.FloatBuffer;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

public final class YuNet implements AutoCloseable {
  private static final int SIZE = 640;
  private final OrtEnvironment env = OrtEnvironment.getEnvironment();
  private final OrtSession session;

  public record Face(Rectangle box, List<List<Double>> points, double score) {}

  public YuNet(String path) throws OrtException {
    try (var opts = new OrtSession.SessionOptions()) {
      opts.setIntraOpNumThreads(2);
      session = env.createSession(path, opts);
    }
  }

  public List<Face> detect(BufferedImage image) throws OrtException {
    double scale = Math.min((double) SIZE / image.getWidth(), (double) SIZE / image.getHeight());
    int w = Math.max(1, (int) Math.round(image.getWidth() * scale));
    int h = Math.max(1, (int) Math.round(image.getHeight() * scale));
    double sx = (double) w / image.getWidth(), sy = (double) h / image.getHeight();
    var padded = new BufferedImage(SIZE, SIZE, BufferedImage.TYPE_INT_RGB);
    var graphics = padded.createGraphics();
    graphics.setRenderingHint(
        RenderingHints.KEY_INTERPOLATION, RenderingHints.VALUE_INTERPOLATION_BILINEAR);
    graphics.drawImage(image, 0, 0, w, h, null);
    graphics.dispose();
    int plane = SIZE * SIZE;
    float[] chw = new float[3 * plane];
    int[] rgb = padded.getRGB(0, 0, SIZE, SIZE, null, 0, SIZE);
    for (int i = 0; i < plane; i++) {
      chw[i] = rgb[i] & 255;
      chw[plane + i] = (rgb[i] >> 8) & 255;
      chw[2 * plane + i] = (rgb[i] >> 16) & 255;
    }
    var candidates = new ArrayList<Face>();
    try (var tensor =
            OnnxTensor.createTensor(env, FloatBuffer.wrap(chw), new long[] {1, 3, SIZE, SIZE});
        var result = session.run(java.util.Map.of("input", tensor))) {
      for (int stride : new int[] {8, 16, 32}) {
        FloatBuffer cls = output(result, "cls_", stride), obj = output(result, "obj_", stride);
        FloatBuffer boxes = output(result, "bbox_", stride),
            points = output(result, "kps_", stride);
        int cols = SIZE / stride;
        for (int i = 0; i < cols * cols; i++) {
          double score = Math.sqrt(Math.clamp(cls.get(i), 0f, 1f) * Math.clamp(obj.get(i), 0f, 1f));
          if (!(score >= 0.85)) continue;
          int x = i % cols, y = i / cols;
          double cx = (x + boxes.get(4 * i)) * stride, cy = (y + boxes.get(4 * i + 1)) * stride;
          double bw = Math.exp(boxes.get(4 * i + 2)) * stride,
              bh = Math.exp(boxes.get(4 * i + 3)) * stride;
          int x0 = Math.max(0, (int) Math.floor((cx - bw / 2) / sx));
          int y0 = Math.max(0, (int) Math.floor((cy - bh / 2) / sy));
          int x1 = Math.min(image.getWidth(), (int) Math.ceil((cx + bw / 2) / sx));
          int y1 = Math.min(image.getHeight(), (int) Math.ceil((cy + bh / 2) / sy));
          if (x1 <= x0 || y1 <= y0) continue;
          var landmarks = new ArrayList<List<Double>>(5);
          for (int j = 0; j < 5; j++) {
            landmarks.add(
                List.of(
                    (x + points.get(10 * i + 2 * j)) * stride / sx,
                    (y + points.get(10 * i + 2 * j + 1)) * stride / sy));
          }
          candidates.add(new Face(new Rectangle(x0, y0, x1 - x0, y1 - y0), landmarks, score));
        }
      }
    }
    candidates.sort(Comparator.comparingDouble(Face::score).reversed());
    var keep = new ArrayList<Face>();
    for (Face face : candidates) {
      if (keep.stream().noneMatch(other -> iou(other.box(), face.box()) > 0.3)) keep.add(face);
    }
    return keep;
  }

  private static FloatBuffer output(OrtSession.Result result, String name, int stride) {
    return ((OnnxTensor) result.get(name + stride).orElseThrow()).getFloatBuffer();
  }

  private static double iou(Rectangle a, Rectangle b) {
    Rectangle overlap = a.intersection(b);
    if (overlap.isEmpty()) return 0;
    double area = overlap.getWidth() * overlap.getHeight();
    return area / (a.getWidth() * a.getHeight() + b.getWidth() * b.getHeight() - area);
  }

  @Override
  public void close() throws OrtException {
    session.close();
  }
}
