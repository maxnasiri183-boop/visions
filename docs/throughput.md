# Operator throughput

Open **Operator feed and search** in Grafana. The first two charts show aggregate and per-source series. The host binds the dashboard to localhost. [Operator dashboard](http://127.0.0.1:13000/d/visions-operator/operator-feed-and-search)

| Measurement | Counting boundary and time basis |
|---|---|
| Observed input FPS | Java emits a frame receipt after FFmpeg decodes a frame and before the detector processes it. Count receipts over five wall-clock seconds. A retry read adds a receipt. The input to this pilot is a local file played as a source. |
| Processed FPS | Count first durable frame commits over five wall-clock seconds, including zero-face frames. The unique key is occurrence and frame index. A checkpoint replay keeps the original frame. |
| Target FPS | Current occurrence frame count divided by source duration in seconds, multiplied by the occurrence's feed speed. The current occurrence takes precedence over queued work. |
| Published observations/s | Independent Kafka consumer counts messages on the operator topic including replay of an existing detection ID. Graph the rate of the per-source counter over one minute. Several observations may originate in one frame. |
| First SQL-visible observations/s | The existing SQL observer polls StarRocks every 250 ms. The first observed row for a detection ID increments its source counter in the same SQLite transaction that saves its first visibility time. Graph the counter rate over one minute. |
| New observations in selected time range | Grafana subtracts each source's first observed cumulative counter from the last observation inside the selected range and sums for the aggregate. It uses the minimum for the first value of a monotonic counter, requires two samples and suppresses intervals that span a counter reset. The result counts whole observations between measured scrape boundaries. The acceptance test reconciles those same integer deltas. [Prometheus query functions](https://prometheus.io/docs/prometheus/latest/querying/functions/) |
| Ingestion to SQL visibility p95 | The interval begins at the detector's UTC frame receipt and ends at the first successful SQL poll. A trailing five-minute window supplies the nearest-rank p95. The all-source value pools the observed intervals. Each source also exports its sample count. |
| Completed missing frames and lag | Completed missing frames compare source frame count to contiguous durable frame checkpoints on completed occurrences. Queue delay uses scheduled wall time. Routine Load lag uses the broker offsets reported by StarRocks. A late frame can still reach its checkpoint. |
| Gallery identities and SQL observations | The published gallery size counts distinct people with a reference in the active gallery version. A separate sampler queries at most once per five minutes. Its retained SQL row count comes directly from the StarRocks primary-key table across all operator runs. Cumulative first-visible observations for the active run continue across retention. A single person can produce many observations. |

After a reader interruption, newly consumed broker records can appear as a short catch-up burst in the publication rate. The observer offset-lag panel shows how much remains to read.

The exporter samples SQLite, its Kafka observer and SQL approximately every four seconds. Prometheus scrapes it every five seconds. Graph gaps indicate missing measurements. **Measurement availability** distinguishes a failed exporter scrape, missing telemetry, an unavailable downstream observer and observed zero traffic. A failed SQL count suppresses the retained-row gauge. A successful sample older than ten minutes also becomes stale. The availability status and sample age in seconds explain gaps. Current gallery versions expose their materialized distinct-person count. Legacy galleries use the five-minute sampler. Source names and partition numbers bound metric cardinality. Detection IDs remain in the private evidence report.

The Kafka observer begins at the then-current end offset on first installation. SQLite stores per-partition next offsets and per-source publication totals atomically. Restart resumes at the stored offset. Messages belonging to another fixture database or an invalid payload increment an unmapped-record diagnostic. If retention has already removed unseen offsets, the reader records how many offsets it missed and resumes at the first available one. The first-SQL-visible table backfills from retained observations plus expiry summaries and updates only on the first visibility transition. Deleting an old clip leaves its cumulative count intact. These two counters therefore have different historical start points.

## Reproduce the two-speed check

Run this on the Docker host after the `replay-lab` source is running and both observers are available. It changes only that source's feed speed. It measures the same labeled clip at 0.5× and 1× for 75 seconds each and returns the source to its previous setting. Normal source admission, existing data and historical observations continue throughout.

```sh
sudo docker exec -i visions-operator-feed-operator-exporter-1 python -u - --seconds 75 < tools/check_operator_throughput.py
```

The test waits for a newly scheduled occurrence after each speed request and genuine decoded frame receipts. The 0.5× interval starts when the occurrence is within one second of schedule. It records observed and processed frame rates, counts zero-face and multi-face frames, and compares the exact set of IDs from completed occurrences with the corresponding StarRocks primary-key rows. For the common Prometheus scrapes inside each wall-clock interval, it compares the publication counter delta against actual Kafka records between their durable offsets. It compares the distinct SQL counter delta against the logical IDs between their SQL watermarks and queries those IDs in StarRocks.

Run `python tools/check_throughput_ledger.py` in the project Python environment for a separate disposable SQLite check of rollback, pre-migration counts, restart, retried offset delivery and retention gaps.

The last check republishes one saved immutable event into the same Kafka partition and waits until Routine Load and the publication counter cross the new offset. The count for that ID must still be one and its first SQL visibility timestamp must remain unchanged. The test prints its private JSON path under `private/operator/throughput/`. Per-scrape counts, UTC observation windows and replay IDs remain in that file.

## Measured on the two-source A100 host

On 2026-11-24 UTC the real `studio` feed continued during both intervals. The `replay-lab` input was the same 150-frame, six-second, 25 FPS clip at both speeds. Each repetition contains 25 frames with two moving faces and 125 empty NOAA frames. Speed changes took effect on a new scheduled occurrence. Every decoded frame was eligible for inference.

| Feed speed | Target FPS | Received / processed frames | Observed input FPS | Processed FPS | Published observations/s | First SQL-visible observations/s | Ingestion → SQL p95 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 0.5× | 12.50 | 969 / 969 | 12.92 | 12.92 | 4.51 | 4.00 | 6.02 s · 300 observations |
| 1× | 25.00 | 1,350 / 1,350 | 18.00 | 18.00 | 6.00 | 6.00 | 7.72 s · 450 observations |

Wall-clock windows were 75.015 and 75.010 seconds. The higher target increased measured processed throughput by 39% under this load. The worker reached 72% of the 25 FPS target. Burst processing and asynchronous SQL polling put some publications and their first SQL sightings in different wall-clock windows. The completed-occurrence missing-frame counter was zero at the end of both checks.

| Exact sampled UTC range | Kafka publications / Prometheus increase | StarRocks distinct IDs / Prometheus increase |
|---|---:|---:|
| 06:37:25.816–06:38:35.816 | 300 / 300 | 300 / 300 |
| 06:38:50.816–06:40:00.816 | 400 / 400 | 400 / 400 |

Each range contained 15 common scrapes across 70 seconds. Whole-clip cross-checks reconciled 250 detection IDs from five completed clips in the slow interval and 400 detection IDs from eight completed clips in the faster interval. An explicit replay advanced Kafka partition 0 from its first event offset 212939 to new offset 214176. Routine Load advanced to 214310 and the primary-key query returned one row before and after replay. The private evidence file on this host is `/home/ubuntu/visions/private/operator/throughput/20261124T063710Z.json`.

An earlier run entered the slow interval with over 100 seconds of old scheduling debt and processed above its requested pace while catching up. The measured comparison above used the revised planner, which drains already scheduled clips and applies the new rate at a fresh wall-clock boundary.
