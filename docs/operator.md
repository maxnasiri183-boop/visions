# A100 operator pilot

## Open the pilot

Run this command on the laptop and keep the SSH session open.

```sh
ssh -N -o ExitOnForwardFailure=yes \
  -L 8092:127.0.0.1:8092 -L 13000:127.0.0.1:13000 \
  -L 8091:127.0.0.1:8091 ubuntu@94.183.176.83
```

| Local browser address | What to do |
|---|---|
| [Live video](http://127.0.0.1:8092/?view=live) | Watch the current private source. Pause, then select a face with the mouse or tab to its box and press Enter. |
| [Recordings](http://127.0.0.1:8092/?view=recordings) | Open a retained airing. Selecting a face updates the URL with its exact occurrence, frame and detection ID. Use the reference record's back control to return to the same observation. |
| [Photo search](http://127.0.0.1:8092/?view=search) | Upload a photo or choose a private example. The second example is outside the gallery. Select its face, search, and open the matching moment in a result. A composed two-person example exercises the face picker. |
| [Reference gallery](http://127.0.0.1:8092/?view=gallery) | Inspect a person's enrollment photo, the rejected-image examples, or an immutable published version. Gallery editing requires the host's private management key in `private/operator/.gallery-admin`. |
| [Source health](http://127.0.0.1:8092/?view=health) | Compare last decoded frame, durable processed frame, latest searchable observation, queue age, schedule lateness and recent gaps. |
| [Operator feed and search in Grafana](http://127.0.0.1:13000/d/visions-operator/operator-feed-and-search) | Check source health, effective frame rates, wall-clock delay, index and SQL visibility, and A100 use. The Routine Load drill-down shows partition progress and the last error. |
| [CPU reference Review](http://127.0.0.1:8091/) | Start a selected-frame acceptance run at speed 1, or reopen and replay a saved observation. The sustained operator feed has its own durable occurrence history. |

The server publishes these browser endpoints on loopback. Local SSH access grants access to the footage and searches. Keep the gallery key on the server and let gallery editors obtain it through their authorized SSH account.

## Actual deployment

The checkout started at `9b1fe7ec8f9d2e4d4a8b4e3bb6d1654677adb332`. At inspection, GitHub main was `6e014c5a0e204a4a2f5c205df57de8522b1860c8`, ten commits behind the deployed feature stack. The separate native HTTP report on port 8080 and its untracked directory belong to the earlier deployment.

Ubuntu 24.04.2 LTS runs Docker Engine with Compose, FFmpeg, ffprobe and the NVIDIA host driver 595.99.02. The CDI device exposes one A100-SXM4-40GB (40,960 MiB) to the Java worker. The operator runs YuNet with ONNX Runtime CUDA and OpenCV SFace on CPU. The earlier provider profile recorded 106 CUDA kernel events and zero CPU kernel events for a YuNet forward. The full-product validation includes recognition, persistence, SQL and index visibility.

| Services | Deployment |
|---|---|
| Kafka, MinIO, StarRocks | Existing persistent Compose backends. The pilot uses a separate topic, bucket and SQL database. Credentials stay server-side. |
| Feeder, detector, exporter | Long-running Compose services. The catalog is re-read during playout. The CUDA worker persists each original payload and Kafka acknowledgement before committing its contiguous frame checkpoint. |
| Recognition, operator UI/API | Long-running Compose services sharing private data. The recognition health check advances only after a successful database poll or batch. |
| Prometheus, Grafana, host GPU and container monitors | Persistent engineering dashboards and bounded metrics. The operator exporter stays on the Compose network. |
| Soak observer | Writes status samples and the final accounting report to the private run directory. The feed stays live after the measurement window. |
| Legacy Review and CPU/CUDA acceptance jobs | The existing Review service remains available. New fixed-clip acceptance and capacity tests run as jobs. |

## Start, restart and verify

For a prepared server, run `tools/start_operator.sh` from this checkout. Use `tools/start_operator.sh --build` after pulling code changes. This uses the existing private catalog, the last running feed ID and the existing storage volumes. An intentional new feed run can specify `FEED_RUN_ID`. Give only trusted operators shell access to the media directory.

For a fresh A100 server, provision the NVIDIA driver and CDI runtime, Docker Compose, FFmpeg with ffprobe, Python 3.12, Node.js 22 and npm. Run `tools/start_workspace.sh --gpu` for the original fixture, private environment file and backend images. Install the pinned Python packages from `operator/requirements.txt` in a virtual environment. Set `OPERATOR_ROOT="$PWD/private/operator"` and `IDENTITY_YUNET="$PWD/private/yunet.onnx"`. Follow the sequence for downloading the private research media, enrolling references, checking capture separation and freezing thresholds. The initial data and model preparation precedes normal startup. [Research setup and evaluation](../identity/README.md)

All original data and derived evidence are under `/home/ubuntu/visions/private/operator`, including `research/archives`, split manifests, model files, gallery snapshots, retained inputs, crops, uploaded probes, SQLite state and private screenshots. There are 10 VidTIMIT identities, 3 enrolled identities, 5 people in the catalog and 18 stream clips including an empty scene, an explicitly composed two-person clip and a degraded-quality test. Portraits and probe photos were exported from independently recorded sessions. Researchers should retain the original source notice and citation with the downloaded data. The pilot selected VidTIMIT after checking the agreement required for PaSC. [VidTIMIT creator and terms](https://www.conradsanderson.id.au/vidtimit/) · [PaSC access](https://cvrl.nd.edu/projects/data/)

Completed occurrences normally retain full evidence for one hour. A bounded list of pinned occurrences protects demonstration URLs. Source archives and the gallery have separate lifetimes. See the measured disk budget and the private retention test. [Continuous feed and retention](operator-feed.md)

To repeat the browser acceptance, install Playwright in the validation environment and provide a Chromium executable. The check uses the private pilot splits, verifies face coordinates and seek targets, and writes screenshots only under the private operator directory.

```sh
CHROME_BIN=/path/to/chrome python tools/check_operator_browser.py
python tools/check_operator_catalog.py
```

Chrome exercised the same two-face frame at 1365 and 390 CSS pixels, fullscreen, keyboard selection, reference navigation and return. Maximum box displacement from the fitted video geometry was 0.34 CSS pixels. An isolated missing-source test advanced from pending inference to a technical error. A 15-second controlled source outage became visible on the Persian source page in 2.44 seconds and cleared in 16.66 seconds. The production instance continued to serve the saved recordings during that outage.

## Pilot measurements

The held-out identification evaluation used a three-person gallery at a frozen cosine threshold of 0.799078. The enrolled result was 1/1 correct (exact two-sided 95% interval 2.5%–100%). Independent unenrolled probes produced 0/3 false identifications (0%–70.76%). The planning target of FPIR ≤0.1% at 1,000 enrollees remains unverified.

The separate retrieval cutoff was 0.374331. Two held-out present probes each had three labeled appearances. All six appeared within the first five results for their respective probes. Macro Recall@1 was 1/3, Recall@5 was 1, and fixed-denominator Precision@5 was 0.60. Both absent probes returned zero results. The expanded test covered 836 frames across 18 distinct clips. [Aggregate results and quality breakdown](../identity/pilot-results.json)

The refactored speed-1 CPU acceptance passed at detection ID `def509ed-1ac9-4b70-ab22-7ef0957ec48c`. The original and replay left one logical SQL row. The complete private artifact is at `/home/ubuntu/visions/private/review-661f0cc187b5/report.json`. [Java characterization](java-worker-validation.md)

Under active load the new Grafana source-health series changed from 1 to 0 and back to 1 during a second controlled interruption. Grafana observed the transitions at 3.96 and 19.12 seconds after the 15-second fault began. The independently collected SQL progress, index backlog and throughput series also changed under real load.

One CUDA source met real-time input rate on the fixed throughput corpus. No tested level met every provisional latency gate. At eight sources, the feed processed 75.83 FPS and the complete pipeline drained at 20.87 FPS. P95 time to searchable results reached 95.98 s with 2,427 observations waiting for CPU recognition. The one-stream comparison measured YuNet inference p95 of 3.24 ms on the A100 and 16.62 ms on CPU. [Full-product CPU and GPU measurements](operator-capacity.md)
