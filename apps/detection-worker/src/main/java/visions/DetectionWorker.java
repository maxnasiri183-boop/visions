package visions;

/** Compatibility entry point for callers using the original fully qualified name. */
public final class DetectionWorker {
  private DetectionWorker() {}

  public static void main(String[] args) throws Exception {
    visions.worker.DetectionWorker.main(args);
  }
}
