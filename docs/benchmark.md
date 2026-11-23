# Sustained detector benchmark

The worker decodes the pinned 512 × 512 lossless feed at 10 frames/s and performs inference on every frame. Each stream has its own FFmpeg decoder, Java worker, ONNX Runtime session and Kafka producer. Every worker publishes real detections through the same S3, Kafka and StarRocks path as the Review workspace. The primary-key SQL table, S3 and Kafka retain this traffic under unique benchmark source IDs.

With the stack running, the fixed fixture in `private/` and the Review Python requirements installed, run from the checkout root as a user with Docker access.

```sh
docker compose build detector
.venv/bin/python tools/benchmark.py --provider cpu
docker compose -f compose.yaml -f compose.gpu.yaml build detector-gpu
.venv/bin/python tools/benchmark.py --provider cuda
```

The default matrix measures 1, 2, 4 and 8 simultaneous streams, sequentially for each provider. Each stream first processes 20 warm-up frames in a separate source namespace. The harness releases all warmed streams together, loops the clip 15 times and checks all 300 measured frames per stream. Use `--streams 1 --loops 2` for a short functional check. Raw worker logs, frame measurements, GPU samples, SQL observations and a summary are saved under `private/benchmarks/`. The GPU path checks the provider request and real first-forward ONNX kernel profile for each session.

## Measurements

- Input is a paced, seekable file with synchronous backpressure. A late worker continues processing in order. Every measured frame must yield fresh inference, and the decoded count must match the input count. Real-time camera drop behavior requires a separate live-source test.
- Source timestamps are continuous media PTS across repeats. The runner uses a synchronized wall-clock interval from barrier release through the final processed frame for throughput. Frame deadlines and stage durations use the JVM monotonic clock. Playout lag is lateness at frame admission. A fast worker can approach 10 FPS per stream at the configured source rate.
- Decode timing covers the wait to read a decoded FFmpeg frame into Java. The decoder also works concurrently. Preprocessing includes resize, color/layout conversion and input tensor creation. Inference covers the ONNX Runtime session call. Crop upload and manifest I/O time S3 requests. Kafka publication includes the producer acknowledgement. Upload and publication percentiles include frames containing detections.
- The SQL observer polls from worker warm-up completion, sleeps 150 ms between queries and timestamps the first poll returning each measured detection. End-to-end latency is first observed SQL visibility minus the wall-clock frame admission timestamp. It includes Routine Load batching and polling delay. All latency percentiles use nearest rank.
- A successful run requires the exact expected SQL observations and frame-level counts, one face for each of frames 0–9 per loop and zero for frames 10–19. Every reported box must reach IoU 0.5 against the manual face box `[176, 66, 277, 182]` (left, top, right, bottom). The unchanged fixture limits quality evidence to this one scene.
- GPU samples are host-wide utilization and used memory sampled approximately once per second while the feed runs. Device allocation and a kernel profile establish actual CUDA use. Batch size is one.

## Capacity criterion

For this local comparison, a configuration keeps pace if aggregate throughput reaches at least 98% of source rate, p95 playout lag stays below 1 s, p95 SQL visibility stays below 10 s, and all label and row checks pass. These are provisional engineering targets for selecting a configuration. The SQL target allows two configured five-second Routine Load intervals. File replay can hide overload behind synchronous backpressure, so the playout-lag criterion matters even when all frames eventually arrive.

## Baseline measurements on 23 November 2026

The host provides 16 vCPUs (AMD EPYC 7763) and one NVIDIA A100-SXM4-40GB. The matrix ran sequentially, using batch size 1 and 20 warm-up frames per stream. Every test produced one SQL row per positive frame, zero false positives on the blank frames, minimum IoU 0.8362, and zero dropped frames. The GPU worker recorded 106 CUDA operator executions and zero CPU operator executions in each profiled forward.

| Provider | Streams | Frames | Duration (s) | Throughput (FPS) | SQL p95 (s) | Playout lag p95 (ms) | ONNX p95 (ms) | Mean GPU (%) | Peak VRAM (MiB) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| cpu | 1 | 300 | 29.95 | 10.02 | 5.49 | 31.8 | 16.17 | 0.0 | 0 |
| cpu | 2 | 600 | 29.96 | 20.02 | 5.53 | 0.4 | 20.74 | 0.0 | 0 |
| cpu | 4 | 1200 | 29.97 | 40.05 | 5.55 | 12.2 | 21.58 | 0.0 | 0 |
| cpu | 8 | 2400 | 30.03 | 79.93 | 5.65 | 311.0 | 37.87 | 0.0 | 0 |
| cuda | 1 | 300 | 30.12 | 9.96 | 5.92 | 279.2 | 8.07 | 1.1 | 481 |
| cuda | 2 | 600 | 29.97 | 20.02 | 5.54 | 141.6 | 6.11 | 3.4 | 959 |
| cuda | 4 | 1200 | 30.04 | 39.95 | 5.62 | 485.4 | 7.00 | 6.6 | 1914 |
| cuda | 8 | 2400 | 30.10 | 79.74 | 6.81 | 935.4 | 9.10 | 13.5 | 3824 |

Eight streams reached the 80 FPS input ceiling with both providers. CUDA reduced p95 ONNX execution time by 76% at eight streams. At that load, the GPU worker spent 31.32 ms in preprocessing, 37.76 ms uploading crops, and 49.96 ms on manifest I/O at p95. Their timings and measured mean GPU utilization of 13.5% point to host work, synchronous storage and source pacing as constraints. The measured path provides little justification for delaying frames to fill a cross-stream inference batch. SQL visibility also includes the independent Routine Load commit interval. These per-stage percentiles are calculated independently and cannot be summed to estimate an end-to-end percentile.

[Full aggregate measurements](benchmark-results.json) include exact counts, per-stream rates, stage timings, GPU samples and tested image IDs. Selected-frame Review jobs measure a different workload. Each sustained row here measures all-frame inference on the specified clip.
