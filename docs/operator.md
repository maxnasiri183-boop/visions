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

For a fresh A100 server, provision the NVIDIA driver and CDI runtime, Docker Compose, FFmpeg with ffprobe, Python 3.12, Node.js 22 and npm. Run `tools/start_workspace.sh --gpu` for the original fixture, private environment file and backend images. Install the operator and optional research commands in a virtual environment with `python -m pip install ./apps/operator ./research/identity`. Set `OPERATOR_ROOT="$PWD/private/operator"` and `IDENTITY_YUNET="$PWD/private/yunet.onnx"`. Follow the sequence for downloading the private research media, enrolling references, checking capture separation and freezing thresholds. The initial data and model preparation precedes normal startup. [Research setup and evaluation](../research/identity/README.md)

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

The [soak report](http://127.0.0.1:8092/api/operator/soak) serves the latest durable status and automatically switches to the final report after the observation window drains. The same run is visible on the Persian source-health page. On this host the measured window began at 2026-11-24 02:46:14 UTC and ends at 2026-11-25 02:46:14 UTC. Its artifacts are under `/home/ubuntu/visions/private/operator/soaks/operator-soak-20261124-a100/`.

## Video workspace interface

The React and TypeScript operator shell is Persian and RTL. The persistent header opens recordings, live sources, image search, and the gallery. Its search dialog (Ctrl/Cmd+K) searches gallery records and recently retained recordings, or opens an exact detection ID. Notifications read the source-health event log. The access menu links the English Operations and Grafana pages. The deployment grants private access through SSH and exposes a separate gallery-management key. The backend supplies no signed-in person profile or individual notification inbox.

The desktop workspace devotes approximately 72% of the available width to the player. The evidence inspector stays beside it. The mobile inspector is a native dialog sheet with focus containment, Escape dismissal and return of focus to the selected box. Changing viewport width keeps the same player. Operator-facing text inherits `lang="fa" dir="rtl"`. Apply LTR isolation to media time, detection IDs, similarity scores and technical payloads. The video, overlays, filmstrip, ruler and progress bars always use physical LTR coordinates. A resize observer fits the overlay to the visible media rectangle, including letterboxing. Local Vazirmatn and its SIL license are in `apps/operator/frontend/public/font/`.

Use the centralized `--bg-*`, `--border-*`, `--text-*`, `--accent*`, `--success`, `--warning`, `--error` and spacing tokens in `apps/operator/frontend/src/style.css`. Primary controls, selection, active navigation and the playhead share the violet accent. The motion curve is `cubic-bezier(0.22, 1, 0.36, 1)`. Hover, button feedback, box selection, changing evidence, inspector reveal and historical navigation use 120, 150, 180, 200, 220 and 240 ms respectively. Keep footage and its coordinate system stationary and honor reduced motion. The approved visual reference is the written operator UI specification in the implementation request. It calls for a video-first composition, side-by-side face evidence and a compact event timeline. Verify these in the running workspace at 1440 px and on mobile.

Comparison retrieves the immutable decision for the selected observation and gallery version. A below-threshold result shows the observed crop, cosine similarity, and a neutral reference placeholder. The technical panel can recompute the pairwise cosine on demand from the stored embeddings. The continuous API has no on-demand image inference or manual replay endpoint. Technical disclosure includes the persisted Kafka offsets, SQL payload and original frame. For another appearance the app submits the observation's detection ID to the existing historical-search endpoint, opens its actual video/frame/face, and encodes the previous selection in the URL. The filmstrip samples the retained playable media; markers reflect observed events in the loaded frame window.

Use the existing deployment script to rebuild and roll out the UI alone.

```sh
cd /home/ubuntu/visions
tools/start_operator.sh --ui-only
```

Run the browser checks on the actual operator endpoint. The recording captures real private media and is stored with the screenshots under `private/operator/screens/video-workspace/`.

```sh
export CHROME_BIN=/tmp/visions-chrome/opt/google/chrome/chrome
.venv/bin/python tools/check_operator_browser.py
.venv/bin/python tools/check_video_workspace.py --record
```

On the pilot, Chromium exercised 1920, 1440, 1280, 820 and 390 px viewports, fullscreen, keyboard operation and reduced motion. The 1440 px player and inspector measured 989 and 385 CSS pixels. The largest measured overlay error across the five sizes was 0.33 CSS pixels. The test also injected a delayed comparison response and an HTTP 503 inside its own browser tab. A current selection survived an actual live-source advance. Global gallery search, source notifications and the engineering links used the running services.

The A100 host runs SFace recognition on the CPU. Its Compose worker uses four OpenCV threads. Set `RECOGNITION_OPENCV_THREADS` to override the worker thread count. A growing interval between frame ingestion and the saved identity decision can leave a detected face pending even when its Kafka event and SQL row are already visible. Compare recognition completions with incoming face observations, then check the age of the oldest pending observation on each source.

Live playback follows a short buffer of committed source timestamps. If detection stops advancing, playback holds at the last processed frame and resumes as new results arrive. Pausing or selecting an observation retains the operator's selected occurrence and frame.
