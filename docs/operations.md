# Pipeline operations

Start the read-only monitoring services from the checkout after starting Review. The GPU sampler needs the same NVIDIA CDI device as the GPU worker. On a CPU-only host, omit `gpu-monitor`.

```sh
mkdir -p private/monitor
chmod g+w private/monitor
docker compose -f compose.yaml -f compose.monitoring.yaml up -d prometheus grafana container-monitor gpu-monitor
docker compose -f compose.yaml -f compose.monitoring.yaml ps
```

Open **Operations** in Review. The page samples the live service protocols, StarRocks ingestion state and host counters every two seconds. It shows the last five minutes of frame-level benchmark telemetry and twelve recent benchmark summaries. The lookup field opens a saved Review observation by its detection ID. For benchmark observations, search the local private worker logs with `rg -F 'detection_id=YOUR_ID' private/benchmarks --glob 'worker-*.log'`.

Forward TCP 13000 on the laptop to 127.0.0.1:13000 on this server in the Review SSH connection. Open [Grafana trends](http://127.0.0.1:13000/d/visions-pipeline/pipeline-operations). Grafana allows read-only viewing through the tunnel. Prometheus scrapes `review:8091/metrics` on the Compose network every five seconds. It retains up to seven days or 512 MB, whichever limit is reached first. The isolated container health collector reads the Docker Engine socket. It has no network interface outside its own loopback, issues a fixed project-filtered list request, and exports only service names, running states and probe states. Review, Prometheus and Grafana do not receive the socket.

## Reading the measurements

- A sustained worker processes each decoded frame synchronously. Decoded and inference FPS use the same completion timestamps. The source FPS is the configured input pace. A PTS deadline missed by over one frame interval flags the stream as behind. A worker that blocks can fall behind without dropping any frames. Selected-frame Review jobs show their intentional sampling separately.
- The last five seconds of each sustained stream supply stage means. Upload and publication include only frames with detections. Pipe-read time includes waiting for FFmpeg; preprocessing and model execution are measured inside the worker. Stage measurements omit work outside their instrumented boundaries.
- SQL latency starts at the UTC worker frame-ingress timestamp and ends at the first client poll that sees its row. The dashboard computes nearest-rank p50 and p95 across first observations in a trailing five-minute window and shows the sample count. PTS tracks media playback independently.
- The topic has one partition. Backlog is the broker's next offset minus one minus the last Routine Load offset applied. Loaded rows include replays. Review shows the logical primary-key row count for a replayed detection.
- Host memory is total minus available. Disk headroom is measured on the private artifact filesystem. CPU busy time excludes idle and I/O wait. GPU utilization and VRAM are device-wide readings from `nvidia-smi`. A stale sampler yields an unavailable reading. Worker provider profiles establish model assignment.
- Ready indicates that the service answered its Kafka offset, MinIO readiness or SQL Routine Load query. The Review health endpoint and the Compose health statuses can also be checked with `docker compose -f compose.yaml -f compose.monitoring.yaml ps`.
- The Grafana panels share the selected UTC time range. Gaps in worker series indicate periods without recent frame samples. Prometheus labels identify the bounded benchmark stream slot, benchmark run and provider. Observation IDs are available in Review and local worker logs.
