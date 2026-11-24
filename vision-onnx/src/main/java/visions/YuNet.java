package visions;

import ai.onnxruntime.*;
import ai.onnxruntime.providers.OrtCUDAProviderOptions;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.awt.*;
import java.awt.image.BufferedImage;
import java.awt.image.DataBufferInt;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.FloatBuffer;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

public final class YuNet implements AutoCloseable {
  private static final int SIZE = 640;
  private final OrtEnvironment env = OrtEnvironment.getEnvironment();
  private final OrtSession session;

  private final ReusableInput reusable;
  private boolean profilePending;
  private double preprocessMs;
  private double inferenceMs;

  public double preprocessMs() {
    return preprocessMs;
  }

  public double inferenceMs() {
    return inferenceMs;
  }

  public record Face(Rectangle box, List<List<Double>> points, double score) {}

  public YuNet(String path) throws OrtException {
    String provider = System.getenv().getOrDefault("ORT_PROVIDER", "cpu");
    boolean cuda = switch (provider) {
      case "cpu" -> false;
      case "cuda" -> true;
      default -> throw new IllegalArgumentException("Unsupported ORT_PROVIDER: " + provider);
    };
    boolean reuse = switch (System.getenv().getOrDefault("YUNET_REUSE_INPUT", "auto")) {
      case "auto" -> cuda;
      case "true" -> true;
      case "false" -> false;
      default -> throw new IllegalArgumentException("YUNET_REUSE_INPUT must be auto, true or false");
    };
    try (var opts = new OrtSession.SessionOptions()) {
      opts.setIntraOpNumThreads(2);
      if (cuda) {
        try (var gpu = new OrtCUDAProviderOptions(0)) {
          gpu.add("cudnn_conv_algo_search", "HEURISTIC");
          opts.addCUDA(gpu);
        }
        opts.enableProfiling("/tmp/yunet-ort");
        profilePending = true;
      }
      System.out.println("onnxruntime_provider_requested=" + provider);
      session = env.createSession(path, opts);
      try {
        reusable = reuse ? new ReusableInput(env) : null;
      } catch (OrtException ex) {
        session.close();
        throw ex;
      }
      System.out.println("reusable_input=" + reuse);
    }
  }

  public List<Face> detect(BufferedImage image) throws OrtException {
    long start = System.nanoTime();
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
    var candidates = new ArrayList<Face>();
    try (var result = inference(padded, start)) {
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

  private OrtSession.Result inference(BufferedImage padded, long start) throws OrtException {
    if (reusable != null) {
      reusable.fill();
      return run(reusable.tensor, start);
    }
    int plane = SIZE * SIZE;
    float[] chw = new float[3 * plane];
    int[] rgb = padded.getRGB(0, 0, SIZE, SIZE, null, 0, SIZE);
    for (int i = 0; i < plane; i++) {
      chw[i] = rgb[i] & 255;
      chw[plane + i] = (rgb[i] >> 8) & 255;
      chw[2 * plane + i] = (rgb[i] >> 16) & 255;
    }
    try (var tensor = OnnxTensor.createTensor(env, FloatBuffer.wrap(chw), new long[] {1, 3, SIZE, SIZE})) {
      return run(tensor, start);
    }
  }

  private OrtSession.Result run(OnnxTensor tensor, long start) throws OrtException {
    long ready = System.nanoTime();
    preprocessMs = (ready - start) / 1e6;
    var result = session.run(java.util.Map.of("input", tensor));
    inferenceMs = (System.nanoTime() - ready) / 1e6;
    if (profilePending) {
      try {
        verifyKernels(Path.of(session.endProfiling()));
        profilePending = false;
      } catch (RuntimeException | OrtException e) {
        result.close();
        throw e;
      }
    }
    return result;
  }

  // Inspect one real inference per GPU session. Subsequent frames are unprofiled.
  private static void verifyKernels(Path profile) {
    int gpu = 0, cpu = 0;
    try {
      var events = new ObjectMapper().readTree(profile.toFile());
      for (var event : events) {
        if (!event.path("name").asText().endsWith("_kernel_time")) continue;
        String provider = event.path("args").path("provider").asText();
        if (provider.equals("CUDAExecutionProvider")) gpu++;
        if (provider.equals("CPUExecutionProvider")) cpu++;
      }
      Files.delete(profile);
    } catch (IOException e) {
      throw new IllegalStateException("Could not verify ONNX Runtime kernel profile", e);
    }
    System.out.printf("ort_profile_cuda_kernels=%d ort_profile_cpu_kernels=%d%n", gpu, cpu);
    if (gpu == 0) throw new IllegalStateException("GPU inference issued no CUDA kernels");
    if (cpu > 0) System.err.println("ONNX Runtime profile detected CPU execution in GPU mode");
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
    if (reusable != null) reusable.tensor.close();
    session.close();
  }
}
