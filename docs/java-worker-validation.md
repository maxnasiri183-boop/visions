# Java worker characterization and regression results

On 24 November 2026 the deployed image and refactored image ran against the same data services and 512 × 512 fixture. CPU and CUDA passed the positive-frame acceptance, exact crop hash and pixel checks, blank-frame check, and the positive and blank replays. Each positive kept one logical SQL row. CPU detection `def509ed-1ac9-4b70-ab22-7ef0957ec48c` and CUDA detection `3de6d9b4-a51e-44ba-8f49-c0eed52fd2d7` identify the refactored positive checks.

| Worker | Streams | Frames | Observed FPS | SQL p95 (s) | Inference p95 (ms) | Playout lag p95 (ms) |
|---|---:|---:|---:|---:|---:|---:|
| Deployed CPU | 1 | 300 | 9.99 | 5.58 | 15.51 | 2508 |
| Refactored CPU | 1 | 300 | 9.97 | 5.50 | 16.38 | 242 |
| Deployed CUDA | 8 | 2400 | 80.11 | 5.56 | 5.00 | 0.38 |
| Refactored CUDA | 8 | 2400 | 80.09 | 5.73 | 6.32 | 708 |

All four 30-second trials decoded every frame, inferred every frame, produced exactly one row per positive frame and preserved minimum IoU 0.8362. GPU profiles had 106 CUDA kernels and zero CPU kernels per profiled stream, with peak VRAM of 3,824 MiB in both eight-stream runs. Throughput changed by less than 0.3%. Stage and playout tails varied while the host also served development traffic. The refactored workers met the provisional throughput, SQL and lag gates in the benchmark guide. The deployed CPU run exceeded the one-second lag target.

The private benchmark directories are `bench-cb4794a30059`, `bench-59b2a993cb70`, `bench-9e4db5323c9f` and `bench-3bcb72eda07f` in table order. Each directory includes the full image ID and sample-level measurements. The continuous tests used three requests in a single JVM per provider, verified all 22 frame events and their original ingest times, and checked the acknowledged replay payload against Kafka and the matching SQL row.
