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
- Decode timing covers the wait to read a decoded FFmpeg frame into Java. The decoder also works concurrently. Preprocessing includes resize, color/layout conversion and preparation of the model input. The CPU reference allocates a tensor per frame. The tuned GPU path refills a shared native buffer. Inference covers the ONNX Runtime session call. Crop upload and manifest I/O time S3 requests. Kafka publication includes the producer acknowledgement. Upload and publication percentiles include frames containing detections.
- The SQL observer polls from worker warm-up completion, sleeps 150 ms between queries and timestamps the first poll returning each measured detection. End-to-end latency is first observed SQL visibility minus the wall-clock frame admission timestamp. It includes Routine Load batching and polling delay. All latency percentiles use nearest rank.
- A successful run requires the exact expected SQL observations and frame-level counts, one face for each of frames 0–9 per loop and zero for frames 10–19. Every reported box must reach IoU 0.5 against the manual face box `[176, 66, 277, 182]` (left, top, right, bottom). The unchanged fixture limits quality evidence to this one scene.
- GPU samples are host-wide utilization and used memory sampled approximately once per second while the feed runs. Device allocation and a kernel profile establish actual CUDA use. Batch size is one.

## Capacity criterion

For this local comparison, a configuration keeps pace if aggregate throughput reaches at least 98% of source rate, p95 playout lag stays below 1 s, p95 SQL visibility stays below 10 s, and all label and row checks pass. These are provisional engineering targets for selecting a configuration. The SQL target allows two configured five-second Routine Load intervals. File replay can hide overload behind synchronous backpressure, so the playout-lag criterion matters even when all frames eventually arrive.

## Measurements on 23 November 2026

The host has 16 vCPUs (AMD EPYC 7763) and one A100-SXM4-40GB. Each stream selects every frame from the same 512 × 512, 10 FPS feed, warms up on 20 frames, and runs with batch size 1. Eight streams are the largest configuration tested. Every completed row below produced zero dropped frames, the exact expected detection counts, minimum box IoU 0.8362 and one SQL record per positive frame. Every profiled GPU forward executed 106 CUDA kernels and zero CPU kernels.

| Detector configuration | Streams | Frames | Duration (s) | FPS | SQL p95 (s) | Playout lag p95 (ms) | Prep p95 (ms) | ONNX p95 (ms) | GPU mean (%) | VRAM peak (MiB) | Meets targets |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| CPU baseline | 1 | 300 | 29.95 | 10.02 | 5.49 | 31.8 | 17.98 | 16.17 | 0.0 | 0 | Yes |
| CPU baseline | 2 | 600 | 29.96 | 20.02 | 5.53 | 0.4 | 19.44 | 20.74 | 0.0 | 0 | Yes |
| CPU baseline | 4 | 1200 | 29.97 | 40.05 | 5.55 | 12.2 | 19.82 | 21.58 | 0.0 | 0 | Yes |
| CPU baseline | 8 | 2400 | 30.03 | 79.93 | 5.65 | 311.0 | 27.82 | 37.87 | 0.0 | 0 | Yes |
| CUDA baseline | 1 | 300 | 30.12 | 9.96 | 5.92 | 279.2 | 30.15 | 8.07 | 1.1 | 481 | Yes |
| CUDA baseline | 2 | 600 | 29.97 | 20.02 | 5.54 | 141.6 | 25.56 | 6.11 | 3.4 | 959 | Yes |
| CUDA baseline | 4 | 1200 | 30.04 | 39.95 | 5.62 | 485.4 | 24.73 | 7.00 | 6.6 | 1914 | Yes |
| CUDA baseline | 8 | 2400 | 30.10 | 79.74 | 6.81 | 935.4 | 31.32 | 9.10 | 13.5 | 3824 | Yes |
| CUDA reusable input | 1 | 300 | 31.79 | 9.44 | 6.15 | 1780.5 | 17.14 | 11.38 | 1.1 | 481 | No |
| CUDA reusable input | 2 | 600 | 29.96 | 20.02 | 5.52 | 283.2 | 13.56 | 6.57 | 2.9 | 959 | Yes |
| CUDA reusable input | 4 | 1200 | 29.95 | 40.07 | 5.48 | 108.6 | 10.62 | 3.89 | 6.2 | 1914 | Yes |
| CUDA reusable input | 8 | 9600 | 119.96 | 80.03 | 5.65 | 205.3 | 11.94 | 5.73 | 11.8 | 3824 | Yes |
| CPU reference | 8 | 9600 | 119.99 | 80.01 | 6.01 | 1833.6 | 24.47 | 27.93 | 0.0 | 0 | No |
| CPU reuse opt-in | 8 | 2400 | 29.98 | 80.06 | 5.67 | 251.4 | 13.27 | 28.94 | 0.0 | 0 | Yes |

At eight streams the baseline GPU reduced p95 ONNX time from 37.87 to 9.10 ms. Preprocessing took 31.32 ms at p95. Reusing the RGB raster and native tensor reduced preprocessing to 15.18 ms in the 30-second prototype A/B and 11.94 ms in the 120-second production CUDA test. The model, crop geometry, batch size and SQL semantics stayed identical across these measurements.

Three additional 30-second prototype runs showed throughput of 9.90, 19.00 and 38.66 FPS at 1, 2 and 4 streams. These trials overlapped deployment or build activity. All missed at least one provisional target. Production-image repeats at two and four streams met all targets. A quiet one-stream production run still missed both the throughput and lag targets. Mean manifest I/O in its first and second halves was 44 and 49 ms. Capacity varies with host and storage conditions.

The eight-stream production CUDA run processed 9,600 frames and yielded 4,800 SQL observations. At p95, manifest I/O took 38.80 ms, crop upload took 34.94 ms, and model execution took 5.73 ms. The five-second Routine Load interval also contributes to end-to-end visibility. These independently computed stage percentiles cannot be summed to predict overall p95. With mean GPU use of 11.8%, the measured path favors a single-frame batch and improving host/storage latency before adding queueing to form larger batches.

For eight streams on this fixture, use the CUDA profile with reusable input and batch size 1. In the matched 120-second tests, 817 of 9,600 CPU-reference frames were over one second late. All 9,600 CUDA frames arrived within 0.515 seconds of their deadlines. Although CPU caught up by the end of the clip, its p95 lag missed the one-second target. CUDA met all three performance targets and the same frame, row and quality checks. CPU buffer reuse is available as an opt-in. Its separate 30-second eight-stream trial also met the targets. Sustained 120-second capacity for that option is unmeasured.

[Full aggregate measurements](benchmark-results.json) include the three contended trials, per-stream rates, stage timings, GPU samples, exact image IDs and sample counts.
