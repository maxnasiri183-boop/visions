# Workspace validation on the A100 host

## Initial inventory

The starting checkout was `main` at `6e014c5a0e204a4a2f5c205df57de8522b1860c8`. Its only uncommitted work was the separate `public-report/` directory. A native Python HTTP server owned TCP 8080. It continues serving that directory.

Ubuntu 24.04 had Docker Engine 29.1.3, Compose v2, FFmpeg, ffprobe, Python 3.12 and NVIDIA driver 595.99.02 installed. Kafka 3.9.1, MinIO and StarRocks 3.4.10 were running. The worker was a job image. The original CPU report and its PNGs survived on disk. That report verified frame 5 on the fixed feed, the S3 crop, offsets 0 and 1, one logical SQL row and 9.137 seconds to first SQL visibility.

PCI identified an A100 SXM4 40 GB. Initially the driver module was unloaded and Docker had no configured NVIDIA device. Loading the installed modules and installing NVIDIA Container Toolkit base 1.20.2 made the GPU accessible through CDI. The CUDA detector subsequently executed 106 CUDA kernels and zero CPU kernels in its profiled YuNet forward.

## Running deployment

| Service | Host endpoint | Purpose and persistence |
|---|---|---|
| Review | 127.0.0.1:8091 | Browser workspace, Operations and bounded CPU worker jobs. Private reports and replay history use the host's private directory. |
| Grafana | 127.0.0.1:13000 | Provisioned dashboard with a persistent Grafana volume. |
| Prometheus | Compose network | Scrapes Review metrics into a bounded persistent volume. |
| GPU monitor and container monitor | No published ports | Write current device and service status into the private monitor directory. |
| Kafka | 127.0.0.1:19092 | Existing topic and Kafka data volume. |
| S3-compatible storage | 127.0.0.1:19000 | Existing private frame manifests and crop objects. |
| StarRocks | 127.0.0.1:19030 | Existing FE metadata and BE data volumes. |

CPU and CUDA CLI detectors run as jobs. Review launches the CPU worker as a subprocess inside its container. The Review service has no Docker socket. The browser reads authorized run artifacts through the API. Keep the deployment on loopback and use the [SSH tunnel](review.md).

## Checks performed

- Chrome played and paused the clip, stepped between frames, clicked the face, read the verified S3 PNG and inspected the matching SQL snapshot. The box position matched the frame coordinates at desktop and mobile widths.
- A new two-times-speed run of frame 5 produced Kafka offset 196. Manual replay produced offset 197, an identical event payload and one logical row. Reopening the run after recreating the Review service recovered both offsets and the original ID.
- A separate frame-15 run read back an empty S3 manifest. Unselected frames displayed the unprocessed state. An isolated QA container with a missing model displayed a processing error; its data directory and endpoint were separate from Review.
- The original CPU SQL row remained after the data service containers were recreated with their existing volumes. The archived PNG served for that run matched the original file byte for byte.
- Live Chrome checks exercised Operations stream selection, detection lookup and Grafana. Prometheus observed the Review target up and the backlog changing under load. Per-worker profiles and device utilization were both observed on a live multi-stream GPU run.

The [benchmark report](benchmark.md) gives the test conditions and measured results. The fixed clip tests a single labeled face scene and a blank scene.
