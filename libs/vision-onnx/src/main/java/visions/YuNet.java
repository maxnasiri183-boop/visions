package visions;

import ai.onnxruntime.OnnxTensor;
import ai.onnxruntime.OrtEnvironment;
import ai.onnxruntime.OrtException;
import ai.onnxruntime.OrtSession;
import ai.onnxruntime.providers.OrtCUDAProviderOptions;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.awt.Rectangle;
import java.awt.image.BufferedImage;
import java.io.IOException;
import java.nio.FloatBuffer;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

public final class YuNet implements AutoCloseable {
  private final OrtEnvironment env = OrtEnvironment.getEnvironment();
  private final OrtSession session;

  private final YuNetInput input;
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
        opts.enableProfiling("/tmp/yunet-ort-" + java.util.UUID.randomUUID());
        profilePending = true;
      }
      System.out.println("onnxruntime_provider_requested=" + provider);
      session = env.createSession(path, opts);
      try {
        input = new YuNetInput(env, reuse);
      } catch (OrtException ex) {
        session.close();
        throw ex;
      }
      System.out.println("reusable_input=" + reuse);
    }
  }

  public List<Face> detect(BufferedImage image) throws OrtException {
    long start = System.nanoTime();
    var candidates = new ArrayList<Face>();
    try (var prepared = input.prepare(image);
         var result = run(prepared.tensor(), start)) {
      for (int stride : new int[] {8, 16, 32}) {
        YuNetOutputs.decodeStride(candidates, stride, output(result, "cls_", stride),
            output(result, "obj_", stride), output(result, "bbox_", stride),
            output(result, "kps_", stride), image.getWidth(), image.getHeight(),
            prepared.sx(), prepared.sy());
      }
    }
    return YuNetOutputs.suppress(candidates);
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

  @Override
  public void close() throws OrtException {
    input.close();
    session.close();
  }
}
