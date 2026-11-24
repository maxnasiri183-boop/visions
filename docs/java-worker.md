# Java detector responsibilities

| Deployed responsibility at 9b1fe7e | Current owner | Verified contract |
|---|---|---|
| DetectionWorker.main and process | DetectionWorker.main constructs dependencies. FrameProcessor orchestrates each decoded frame. | Frame index, PTS, detection event fields, manifest URI and frame_stats lines remain compatible. |
| DetectionWorker.makeEvents | FrameProcessor associates faces with observation IDs. CropStore cuts the original pixels and writes the PNG to S3. | The pinned positive frame has box (178, 63, 92, 112). The stored crop matches the original pixels and checksum. |
| DetectionWorker.process conditional S3 operations | ObservationStore owns manifest lookup, conditional creation and loading the winner after an S3 conflict. | Replay reuses the canonical event bytes, including the original ID, ingest time and crop. An empty array is a processed zero-face frame. |
| DetectionWorker.process synchronous Kafka send | EventPublisher waits for the broker acknowledgement for each winner event. | A broker failure propagates before successful frame progress is recorded. |
| YuNet.detect input preparation | YuNetInput resizes and pads the frame, builds BGR planes and owns reusable CUDA input storage. | CPU can still use per-frame input allocation. Each GPU worker retains its own direct tensor until close. |
| YuNet.detect inference, geometry and suppression | YuNet owns the ORT session, timing and kernel profiling. YuNetOutputs decodes stride tensors, clips source-space boxes and suppresses overlaps in descending score order. | Confidence, five landmarks, box geometry and actual CUDA execution are checked. |

With initialized Compose services and the pinned fixture, run `python tools/characterize_worker.py` in the Review virtual environment. For CUDA, set `COMPOSE_FILE=compose.yaml:compose.gpu.yaml DETECTOR_SERVICE=detector-gpu`. The test uses fresh sources for positive and empty frames and replays both. Java unit tests run with `gradle test` on JDK 21.
