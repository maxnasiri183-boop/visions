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

## Continuous file clips

For a single seekable clip, run the worker with `CLIP SOURCE_ID continuous SPEED START_INDEX`. The zero-based start index is inclusive. The worker decodes past the prefix without pacing, inference or publication for skipped frames. By default the first resumed frame starts the playback clock. A resident request can supply numeric `scheduled_at` in Unix epoch seconds to anchor the first PTS of the *whole occurrence*. Frames are due at that wall-clock anchor plus the PTS displacement divided by speed, converted to a monotonic clock for pacing. Late frames run immediately. A restart retains the original schedule, and frame_stats.lag_ms measures admission lateness against it. An index equal to the clip length succeeds immediately.

For consecutive clips, start the worker with the single argument `serve`. After `worker_ready`, send one JSON request per line to stdin. Keep one request in flight per worker. Keep stdin open across requests. Close it to end the worker. The resident JVM reuses its ORT session, buffers, S3 client and producer. The process exits with failure if it cannot finish a request. The caller may restart at its last committed next-frame checkpoint.

```json
{"request_id":"clip-job-1","clip":"/input/feed.mkv","source_id":"camera-a-clip-41","speed":1,"start_index":0}
```

Each completed frame emits one stdout line beginning with `frame_event=`. The JSON contains schema_version, source_id, asset_sha256, detector_version, video_track, frame_index, pts, time_base, frame_size, frame_ingested_at, manifest_key and detections. Each element of detections contains the canonical detection payload under `event`, plus the acknowledged topic, partition and offset. The worker emits an empty detections array for a processed zero-face frame. It emits the frame only after the S3 manifest is durable and every Kafka send has been acknowledged. A crash after publication can repeat a Kafka payload on resume. The durable manifest keeps its ID and original content stable.

New manifests also record the first ingest timestamp in S3 object metadata. Cached legacy face frames recover that timestamp from the event. Legacy empty arrays without metadata yield a null frame_ingested_at. At EOF the worker emits `clip_done=` with request_id, source_id and frame_count. `DETECTION_BUCKET` and `DETECTION_TOPIC` default to the original stock services' bucket and topic. Provision any explicitly configured operator destinations before starting the worker.

Run `python tools/check_frame_events.py` against the original acceptance bucket/topic to exercise nonzero resume, real Kafka offsets, empty observations, replay, EOF and SQL cardinality.
