package visions;

import ai.onnxruntime.OnnxTensor;
import ai.onnxruntime.OrtEnvironment;
import ai.onnxruntime.OrtException;
import java.awt.Color;
import java.awt.RenderingHints;
import java.awt.image.BufferedImage;
import java.awt.image.DataBufferInt;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.FloatBuffer;

final class YuNetInput implements AutoCloseable {
  static final int SIZE = 640;
  private final OrtEnvironment env;
  private final ReusableInput reusable;

  record Prepared(OnnxTensor tensor, double sx, double sy, boolean transientTensor) implements AutoCloseable {
    @Override
    public void close() {
      if (transientTensor) tensor.close();
    }
  }

  YuNetInput(OrtEnvironment env, boolean reuse) throws OrtException {
    this.env = env;
    reusable = reuse ? new ReusableInput(env) : null;
  }

  Prepared prepare(BufferedImage image) throws OrtException {
    double scale = Math.min((double) SIZE / image.getWidth(), (double) SIZE / image.getHeight());
    int w = Math.max(1, (int) Math.round(image.getWidth() * scale));
    int h = Math.max(1, (int) Math.round(image.getHeight() * scale));
    double sx = (double) w / image.getWidth(), sy = (double) h / image.getHeight();
    var padded = reusable == null ? new BufferedImage(SIZE, SIZE, BufferedImage.TYPE_INT_RGB) : reusable.raster;
    var graphics = padded.createGraphics();
    if (reusable != null && (w != SIZE || h != SIZE)) {
      graphics.setColor(Color.BLACK);
      graphics.fillRect(0, 0, SIZE, SIZE);
    }
    graphics.setRenderingHint(
        RenderingHints.KEY_INTERPOLATION, RenderingHints.VALUE_INTERPOLATION_BILINEAR);
    graphics.drawImage(image, 0, 0, w, h, null);
    graphics.dispose();
    if (reusable != null) {
      reusable.fill();
      return new Prepared(reusable.tensor, sx, sy, false);
    }
    int plane = SIZE * SIZE;
    float[] chw = new float[3 * plane];
    int[] rgb = padded.getRGB(0, 0, SIZE, SIZE, null, 0, SIZE);
    for (int i = 0; i < plane; i++) {
      chw[i] = rgb[i] & 255;
      chw[plane + i] = (rgb[i] >> 8) & 255;
      chw[2 * plane + i] = (rgb[i] >> 16) & 255;
    }
    return new Prepared(OnnxTensor.createTensor(env, FloatBuffer.wrap(chw), new long[] {1, 3, SIZE, SIZE}), sx, sy, true);
  }

  // Each worker owns one session and processes frames serially. The native tensor
  // shares the direct buffer's storage; no previous inference is in flight at refill.
  private static final class ReusableInput {
    final BufferedImage raster = new BufferedImage(SIZE, SIZE, BufferedImage.TYPE_INT_RGB);
    final FloatBuffer channels = ByteBuffer.allocateDirect(3 * SIZE * SIZE * Float.BYTES)
        .order(ByteOrder.nativeOrder()).asFloatBuffer();
    final OnnxTensor tensor;

    ReusableInput(OrtEnvironment env) throws OrtException {
      tensor = OnnxTensor.createTensor(env, channels, new long[] {1, 3, SIZE, SIZE});
    }

    void fill() {
      int[] rgb = ((DataBufferInt) raster.getRaster().getDataBuffer()).getData();
      int plane = SIZE * SIZE;
      for (int i = 0; i < plane; i++) {
        channels.put(i, rgb[i] & 255);
        channels.put(plane + i, (rgb[i] >> 8) & 255);
        channels.put(2 * plane + i, (rgb[i] >> 16) & 255);
      }
    }
  }

  @Override
  public void close() {
    if (reusable != null) reusable.tensor.close();
  }
}
