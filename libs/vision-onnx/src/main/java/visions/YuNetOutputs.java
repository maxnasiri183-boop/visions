package visions;

import java.awt.Rectangle;
import java.nio.FloatBuffer;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

final class YuNetOutputs {
  static void decodeStride(List<YuNet.Face> candidates, int stride, FloatBuffer cls, FloatBuffer obj,
      FloatBuffer boxes, FloatBuffer points, int width, int height, double sx, double sy) {
    int cols = YuNetInput.SIZE / stride;
    for (int i = 0; i < cols * cols; i++) {
      double score = Math.sqrt(Math.clamp(cls.get(i), 0f, 1f) * Math.clamp(obj.get(i), 0f, 1f));
      if (!(score >= 0.85)) continue;
      int x = i % cols, y = i / cols;
      double cx = (x + boxes.get(4 * i)) * stride, cy = (y + boxes.get(4 * i + 1)) * stride;
      double bw = Math.exp(boxes.get(4 * i + 2)) * stride,
          bh = Math.exp(boxes.get(4 * i + 3)) * stride;
      int x0 = Math.max(0, (int) Math.floor((cx - bw / 2) / sx));
      int y0 = Math.max(0, (int) Math.floor((cy - bh / 2) / sy));
      int x1 = Math.min(width, (int) Math.ceil((cx + bw / 2) / sx));
      int y1 = Math.min(height, (int) Math.ceil((cy + bh / 2) / sy));
      if (x1 <= x0 || y1 <= y0) continue;
      var landmarks = new ArrayList<List<Double>>(5);
      for (int j = 0; j < 5; j++) {
        landmarks.add(
            List.of(
                (x + points.get(10 * i + 2 * j)) * stride / sx,
                (y + points.get(10 * i + 2 * j + 1)) * stride / sy));
      }
      candidates.add(new YuNet.Face(new Rectangle(x0, y0, x1 - x0, y1 - y0), landmarks, score));
    }
  }

  static List<YuNet.Face> suppress(List<YuNet.Face> candidates) {
    candidates.sort(Comparator.comparingDouble(YuNet.Face::score).reversed());
    var keep = new ArrayList<YuNet.Face>();
    for (YuNet.Face face : candidates) {
      if (keep.stream().noneMatch(other -> iou(other.box(), face.box()) > 0.3)) keep.add(face);
    }
    return keep;
  }

  private static double iou(Rectangle a, Rectangle b) {
    Rectangle overlap = a.intersection(b);
    if (overlap.isEmpty()) return 0;
    double area = overlap.getWidth() * overlap.getHeight();
    return area / (a.getWidth() * a.getHeight() + b.getWidth() * b.getHeight() - area);
  }

}
