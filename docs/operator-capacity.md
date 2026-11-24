# Complete operator product capacity

These trials exercise the operator playlist, scheduled file playout, CUDA YuNet detection, crop and manifest persistence, acknowledged Kafka publication, StarRocks Routine Load, SFace alignment and embedding, versioned recognition, and filtered similarity search. The private SQL observer verifies the row fields against every published observation. The independent search observer waits for a committed, queryable embedding. Results measure this hardware, gallery, corpus, concurrent pilot and time window.

## Reproduction

Start the regular Compose backends and build the operator feeder and worker images. Prepare the private research corpus with a published gallery and a held-out upload. Use an empty directory for the trials. The host Python environment needs the same OpenCV, Flask and numerical dependencies as the recognition service, and Docker access directly or through noninteractive sudo. The harness uses the private Docker network and does not open another browser port.

```bash
.venv/bin/python tools/operator_capacity.py \
  --source "$PWD/private/capacity-corpus-v1" \
  --yunet "$PWD/private/yunet.onnx" \
  --background-feed-db "$PWD/private/operator/feed.sqlite" \
  --output "$PWD/private/capacity-$(date -u +%Y%m%dT%H%M%SZ)" \
  --streams 1 2 4 8 --cycles 1
```

Each level starts with an empty observation database and an empty embedding index. Only gallery membership, reference vectors and policy versions are copied. Warm-up runs on separate source/occurrence IDs. The harness waits for every warm source to stop scheduling and its data to become visible before enabling measured sources. The measured playlist, original assets, speed, enrollment and thresholds stay fixed. Every frame must have fresh detector inference. All observation IDs must be distinct, have a Kafka acknowledgement, match the full SQL payload and refer to a decision from the frozen gallery. Every accepted observation must have a 128-component embedding and an observed searchable time. A held-out probe exercises the product's Flask upload and search request handlers against each level's live index. Search handler latency excludes HTTP transport and probe creation.

The benchmark removes its unique Compose project after each run. Its raw local evidence remains private. Run the following after auditing a trial to expire its remotely retained test crops, manifests and SQL rows through the product's normal retention worker. The command makes a local evidence database backup and checks namespace ownership and Routine Load progress. The shared topic follows its configured retention.

```bash
.venv/bin/python tools/operator_capacity_expire.py \
  --yunet "$PWD/private/yunet.onnx" \
  "$PWD"/private/capacity-YYYYMMDDTHHMMSSZ/*-streams
```

## Workload and measurement

One pass contains five single-person VidTIMIT excerpts, a two-person spatial composite from the same corpus, a derived quality-stress clip and a NOAA satellite scene with zero faces. Each source has 298 frames at 512 × 384, 50 at 1024 × 384 and eight at 678 × 678, totaling 356 frames and 398 labeled person appearances over 31.84 seconds. The first seven clips run at 12.5 FPS and the last runs at 2 FPS. Pacing uses source timestamps at speed 1. All frames go through batch-one CUDA detection. The SFace recognizer and exact similarity index use one resident CPU worker per trial with a three-person gallery. The independently running pilot shares the GPU, CPU, disk and streaming backends.

The numeric evidence contains UTC start and finish, warm-up counts, actual pilot frame counts, queue maxima, host samples, and nearest-rank p50, p95, maximum and sample count by stage. Frame processing rate uses the first scheduled PTS through the last locally committed frame. Complete rate uses the same start through the final frame commit, SQL observation and recognition/search observation, whichever is latest. The final 2 FPS clip may commit its last frame before its nominal duration elapses. Pipeline latency starts at actual wall-clock frame admission. Playout lag separately measures lateness against the scheduled PTS. SQL polls at 250 ms. Search visibility polls at 200 ms. Candidate counts and handler timings reflect actual source/date filters and available index size at query time.

The earlier benchmark's provisional criteria are at least 98% of source frame rate, p95 playout lag below 1 s and p95 SQL visibility below 10 s. This test additionally checks for p95 searchable visibility below 5 s and no processing errors. The measured corpus and source frame rates differ from the older 512 × 512 detection-only result.

## Decoder regression

Multiple occurrences of the same movie shared one OpenCV read cursor. Interleaved observations at different positions forced repeated seeks before alignment. In arrival order from the preliminary two-source trial, reading 70 frames with a path-keyed cursor took 12.312 s (p50 224.7 ms, p95 330.9 ms). A bounded cursor per occurrence took 0.952 s (p50 12.8 ms, p95 15.9 ms). The concatenated decoded image bytes matched exactly. A maximum of eight cursors is retained.

```bash
.venv/bin/python tools/check_operator_decoder.py "$PWD/private/capacity-YYYYMMDDTHHMMSSZ/2-streams"
```

The preliminary two-source product run reached 12.06 FPS through drain, with p95 searchable in 30.83 s and a peak recognition backlog of 589. The decoder comparison used one additional CPU process for part of that preliminary run. The corrected two-source run reached 20.41 FPS through drain, with p95 searchable in 7.13 s and a peak backlog of 184. Each produced 782 fresh SQL observations and 708 searchable embeddings on the same labeled frames. Runtime kernel profile filenames also now include a unique identifier so multiple detector JVMs can profile concurrently in one container.

## Measured product capacity on 24 November 2026

The host ran Ubuntu 24.04.2 LTS with 16 AMD EPYC 7763 vCPUs and one NVIDIA A100-SXM4-40GB, driver 595.99.02. The CUDA worker reused its tensor input. The matched CPU worker used the identical worker image and tensor preprocessing, registered only the CPU provider and received zero device mappings or GPU requests. The pilot processed 12.4–13.0 background FPS in the measured windows. Host GPU metrics include that pilot.

| Detector | Sources | Offered FPS | Committed feed FPS | Complete FPS | P95 playout lag (s) | P95 SQL (s) | P95 searchable (s) | Peak recognition backlog | Host GPU p95 (%) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CUDA | 1 | 11.18 | 11.35 | 11.35 | 1.75 | 17.33 | 1.15 | 21 | 6 |
| CUDA | 2 | 22.36 | 22.69 | 20.41 | 1.73 | 16.90 | 7.13 | 184 | 9 |
| CUDA | 4 | 44.72 | 45.31 | 20.55 | 2.38 | 15.26 | 38.78 | 982 | 14 |
| CUDA | 8 | 89.45 | 75.83 | 20.87 | 8.67 | 18.75 | 95.98 | 2427 | 18 |
| CPU | 1 | 11.18 | 11.34 | 11.34 | 2.43 | 16.84 | 0.79 | 13 | 3 |

Every corrected trial processed the entire playlist, with zero intentionally omitted frames, zero drops, zero worker retries, and zero recognition processing errors. Per source, the detector produced the expected face count on 349/356 frames. It missed seven faces on the quality-stress clip, detected both faces on all 50 two-person frames, and detected zero on every satellite frame. Every one of the 391 observations appeared in SQL. The recognizer created 354 searchable embeddings and returned 37 poor-quality decisions. It correctly matched 82 of 241 observations of enrolled people and returned unknown on all 150 unenrolled observations. The remaining 122 enrolled observations received unknown. There were zero wrong positive identifications on this repeated workload. The highest-ranked result matched the labeled probe identity on every search that returned a group. At eight sources this was 27/27 such queries against up to 354 eligible embeddings for each source. These are repeated load checks with three enrolled identities. The independent, held-out identification evaluation and the gallery-scale FPIR target have different denominators.

At one source, SQL visibility dominates. Of the 17.33 s CUDA p95 from frame admission to SQL, 17.27 s p95 elapsed after the local frame commit. The concurrent StarRocks job showed one RUNNING task, five-second max batch interval, fifteen-second task consumption, and zero rejected rows. The separate recognition index became searchable in 1.15 s p95. The two-person clip and the clip immediately following drove the one-source CUDA playout lag above one second. Every profile from every CUDA process verified 106 CUDA kernels and zero CPU kernels.

At two sources, the single CPU recognition worker started to accumulate more observations than it could align, embed and index during playback. At four and eight sources, complete throughput leveled near 21 FPS while its queue rose to 982 and 2,427 observations. At eight, retained-frame retrieval and SFace embedding took 27.18 and 32.89 ms p95, respectively. CUDA inference took 5.20 ms p95, and the detector also fell behind the 89.45 FPS input rate. The eight-source measurement lasted 136.46 seconds through complete visibility. Host GPU p95 utilization was 18%, peak device usage was 4,304 MiB, and host CPU p95 was 85.6%. The workload provides no evidence that increasing GPU batch size would address the recognition queue.

One GPU source kept search p95 below five seconds and delivered the full source frame rate through both destinations. No tested configuration met every provisional latency gate. CPU-only detector inference on the one-source reference took 16.62 ms p95 versus 3.24 ms on CUDA. Its frame pace also exceeded 98% of input, but playout lag and SQL visibility missed their thresholds even at one source. The CPU scaling trial stopped there to hand the host to the 24-hour soak. Sustained production capacity at the user’s latency and recognition-quality targets remains unestablished.

[Numeric stage, quality, host and queue measurements](operator-capacity-results.json)
