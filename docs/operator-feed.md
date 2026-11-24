# Continuous private playlist

The planner and detector run in an independent Compose project on the existing `visions_default` network. They use the private operator bucket `visions-operator`, Kafka topic `face.operator.v1` and StarRocks database `visions_operator`. The single-frame CPU/GPU acceptance services keep their previous resources. The persistent detector retains its CUDA session across clips. The worker checksums the real crop from private S3 before committing a frame, observations and their checkpoint to SQLite WAL in a single transaction.

The private catalog is a JSON list of entries with source_id (camera), asset_id, session_id, path relative to the data directory, sha256 and synthetic flag. Identifiers may contain ASCII letters, digits, period, underscore and hyphen. A planner rescan imports any new asset by checksum, retains a lossless copy of its original file and produces a seekable MP4. Keep source clips, catalog, model files, media copies, crops, embeddings, SQLite files and browser screenshots in the ignored private directory. Sources rotate independently through their ordered clips. Supplied public research recordings remain prerecorded sources. A composite receives the synthetic flag.

## Deployment

From the checkout containing the Java continuous worker, build the two operator images. The existing review image supplies Python and its dependencies. Keep the NVIDIA driver on the host and expose its CDI device to the operator worker.

```sh
docker build -f Dockerfile.gpu -t visions-detector-gpu-operator:local .
export OPERATOR_DATA_HOST="$PWD/private/operator" YUNET_MODEL_HOST="$PWD/private/yunet.onnx"
export FEED_RUN_ID=operator-day-01 FEED_RUN_HOURS=24 FEED_KEEP_LIVE=1
docker compose --env-file .env -f compose.feed.yaml build
docker compose --env-file .env -f compose.feed.yaml up -d
```

Use the same FEED_RUN_ID on service restart. A fresh run name starts another occurrence history. FEED_WORKERS controls concurrent resident workers (1–8), FEED_SPEED controls playout speed, and FEED_QUEUE_PER_SOURCE bounds the outstanding occurrence count (default 2). The planner records a due time and duration per occurrence. Recovery processes missed media in order; Operations reports delayed source time and queue age. With FEED_KEEP_LIVE=1, the configured horizon closes only the soak observation window; admissions continue. Use FEED_KEEP_LIVE=0 for a bounded test that stops new admissions and drains its queue. Identity is deployed separately and consumes committed observations. Services expose no public HTTP port.

## Catalog preparation

The planner stages new private media on one background thread. At most two file jobs may be outstanding. Existing sources continue admitting occurrences on their own clocks while a checksum, frame probe or seekable MP4 is in progress. Retained files are verified first after restart. An existing playlist keeps its ready members during additions, then switches to the complete updated list. A removed asset leaves the rotation immediately. A missing or invalid input marks the affected source unavailable and an explicit source interruption takes effect during preparation.

Inspect `catalog-preparation` in `feed.sqlite.service_state` for the outstanding asset count. A new source has state `preparing` until it can play a verified clip. An existing source can be `online` with pending additions in its detail. Run `python tools/check_operator_catalog.py` with the repository's Python dependencies and FFmpeg to check that both cameras admit new occurrences while a real FFmpeg preview is held, and that the planner can resume a contiguous sequence after restart.

## Evidence and recovery

`feed.sqlite` stores source occurrences, frame timestamps, dimensions, original detection JSON, original/latest Kafka offsets, first externally observed SQL row and its matching ID, and local crop/clip paths. Each source repetition receives a new occurrence ID. A restarted worker reuses the persisted occurrence ID and reprocesses the last committed frame before advancing. Replays require identical full detection payload and a later Kafka offset, then retain one local observation row. Frame indices commit contiguously. Zero-face frames persist explicitly. Uncommitted frames remain pending or retrying. `runs`, `run_totals`, `recoveries`, `notices` and `service_state` support diagnostics.

`sql_visible_at` is recorded when an independent SQL query first sees the row. `search_visible_at` is recorded by a separate observer that polls for the committed, ready, nonempty embedding in the searchable identity table approximately every 200 ms. These are wall-clock observations and include polling delay. The original media PTS and time base are persisted independently.

For a controlled restart test, run the following command with the same Compose environment as the deployment and a live positive-face source. A wrapper script may carry the Compose options and environment.

```sh
python3 tools/operator_recovery.py --data "$OPERATOR_DATA_HOST" --run "$FEED_RUN_ID" ./operator-compose-wrapper
python3 tools/operator_outage.py --data "$OPERATOR_DATA_HOST" --source studio --seconds 15
```

The first command stops and starts the feeder and abruptly stops and starts the separate worker. It verifies the resumed frame, actual replay offsets, identities and complete original frame count, and writes private recovery evidence. The second pauses new source admissions until its deadline. Queued work may still complete during that interval.

## Storage on the measured host

The pilot recorded 4,335 frames in a 393 s window. Local crops occupied 302 MB and S3 occupied 361 MB, for a projected combined 6.1 GB/hour with that scene mix. Reusable retained video occupied 60 MB. The filesystem had 98.5 GB available at measurement time. The default 24 GiB free-space reserve pauses admissions and processing under storage pressure. This load estimate depends on face density, resolution and compression.

Completed occurrences expire after one hour by default (FEED_RETENTION_SECONDS). The worker tags new operator objects as rolling on the original put. A lifecycle rule expires rolling-tagged objects after one day as a crash-orphan safeguard. Completed pins receive a protected tag and have no lifecycle expiry. The janitor removes private crops, manifests, SQL rows, frame and observation payloads; the identity worker removes expired embeddings and decisions. SQLite reuses freed pages. The small occurrence record retains expiry status and counters. Shared retained media is stored once per digest and is reclaimed when the last retained occurrence expires and the asset leaves the catalog. The original supplied source file, gallery and model inputs remain separately under operator control. Kafka rotates segments after five minutes and retains the operator topic for up to two hours or 256 MiB per partition plus the active segment. StarRocks physical compaction and broker segment disposal can lag logical expiry. Validate free space independently on each backing volume if the stack uses multiple filesystems.

A private `pinned.json` array may identify up to eight stable demonstration occurrence IDs. These are excluded from occurrence pruning and retagged in S3. On the tested host, both configured demo pins carried the protected tag and the live rolling frame carried the lifecycle tag. The production retention test used a separate project and 25-second window. It observed 50 rows, 50 embeddings, 50 crops and 50 frame manifests before expiry. All checked counts fell to zero, including remote SQL, one directly checked S3 crop, and the derived media after the test clip left its catalog. The supplied source file remained.

The independent smoke restart test resumed planner sequence 20 at 21. Abrupt detector restart resumed durable frame 4 with Kafka offset advancing from 1107 to 1108. The observer saw replay 8.08 s after the start command returned (9.54 s including stop/restart). All 50 source frames reached durable storage without gaps.

## Monitoring and uninterrupted soak

Start the exporter and the independent observation process alongside the pipeline.

```sh
export FEED_KEEP_LIVE=1
docker compose --env-file .env -f compose.feed.yaml -f compose.feed.monitoring.yaml up -d
```

The internal exporter serves JSON on `http://operator-exporter:9125/summary` and Prometheus exposition on `/metrics`. Grafana provisions **Operator feed and search** with source targets, processed rate, queued occurrence age, stage timings, search/SQL visibility, Routine Load and shared device readings. Per-frame IDs remain in private databases. Rates use a 5-second window and latency percentiles use a 300-second window with sample counts. Source input FPS is a file-playout target. The independent camera ingress count is unavailable for this playlist. Queue age measures wall time since the oldest due occurrence, while current frame lateness measures decoded media position against its scheduled deadline. Timings from a paused/replayed source can include catch-up. The pilot recovered from 19.82 s behind schedule to 0.11 s across 34 successive occurrences after interruptions.

The soak monitor writes `soaks/<run_id>/samples.jsonl`, an atomically replaced `status.json` and terminal `report.json` under private operator storage. It samples every ten seconds, records missing observation intervals and flags any source without durable frame progress for 45 seconds. It checks cumulative expected versus committed frame counts, missing scheduled coverage, backend heartbeat, disk headroom, SQL visibility and identity state. Source count is derived from committed file frames. The monitor never claims independent camera packet receipt. An expired observation remains accounted for in its occurrence's durable last-frame index. The final report filters by occurrences scheduled during the observation window, waits for their downstream drain, and records any failures. FEED_KEEP_LIVE keeps the same sources active afterward.
