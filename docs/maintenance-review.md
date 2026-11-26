# T4 maintenance review

Baseline on the T4 VM was main at `97ef061`, with one worktree, no local-only commits, no staged or unstaged source and no untracked source. Production images reported `unrecorded`. The feed Python, identity package, operator server, compiled browser assets and Review app matched the checkout by file comparison. Mounts and image IDs were recorded alongside a verified local Git bundle under the private VM baseline directory. The models, databases, environment files and footage belong to the VM.

## Ownership and priorities

| Priority and change cost | Owner, caller and finding | Change and check |
|---|---|---|
| High risk, medium effort | `FrameProcessor.process` constructs the S3 manifest key, interprets legacy ingest metadata and reaches inside `ObservationStore` for the bucket. This couples replay and worker output to the S3 layout. | Move key construction and recovered timestamp ownership into the manifest store. Run conditional-conflict tests and real positive/empty replay. |
| Medium risk, low effort | `Detection` exposes JDK data, but `:contracts` exports Jackson to the entire worker. `DetectionWorker` and `ClipTask` consume Jackson directly. | Declare Jackson in its actual consumers. Compile all three modules and compare emitted JSON. Keep ONNX Runtime exported where public signatures contain OrtException. |
| High risk, medium effort | `identity.worker.Decoder.frame` and `operator.server.original` both own OpenCV seek/read/release behavior. An incorrect seek associates evidence with a different face. | Give retained-frame decoding one owner, preserving the recognition cache and per-request lifetime for HTTP. Compare source pixels and exercise missing frames. |
| Medium risk, low effort | `deploy/detections.sql` is a StarRocks provisioning input for both operator feed and engineering acceptance. The image-specific build contexts and host runner must agree on its location. | Name the owning deployment subsystem, migrate all build contexts and test provisioning from the resulting images. |
| Medium risk, medium effort | Operator entry points construct occurrence/frame/face links independently. Recorded and appearance players independently subscribe to presented-frame timing. | Centralize evidence navigation and presented-frame subscriptions while retaining appearance-bounded playback. Exercise selection, return navigation and gaps in the browser. |

## Dependency map and retained boundaries

* Java: `detection-worker` (CLI and resident JSON commands) depends on `contracts` (detection event) and `vision-onnx` (YuNet preprocessing, inference and outputs). `FileFeed` owns decode order and media pacing. `CropStore`, `ObservationStore` and `EventPublisher` are runtime adapters. Keep conditional S3 publication, Kafka acknowledgment before receipt and the independently implemented pixel oracle in `tools/acceptance.py`.
* Python: `feedsimulator` publishes video; `feed` plans or records occurrences, runs the resident JVM, checkpoints frames, observes SQL/search visibility, expires footage and serves metrics. `identity` enrolls, publishes, indexes and freezes decisions. Research imports, calibration, capacity and corpus evaluators have separate CLI entry points. `vision.py`, `samples.py` and the notebook are standalone examples.
* HTTP and UI: `operator/server.py` serves operator evidence and the React build with identity blueprints. `web/app.py` runs engineering fixture jobs. `web/operations.py` serves diagnostics. Engineering tooling and browser/contract tests live in `tools`; Java characterization tests live in each module. No repository CI workflow was present at baseline.
* Deployment: root Compose files preserve project-relative contexts, network names and data volumes. Dockerfiles package Java, feed, review and operator independently. StarRocks DDL, Prometheus, Grafana, GPU/container collectors and deployment shell scripts are deployment inputs. They do not implement recognition policy.

## Route inventory before and after

| URL on VM loopback | Audience and capability | Persistence and compatibility |
|---|---|---|
| 8092, including live, recordings, detections, search, gallery, identified and health | Canonical Persian RTL operator shell. Frame/evidence APIs under /api/operator, gallery/search under /api/identity. | Existing query keys, including occurrence, frame, selected detection and return location, remain addressable. |
| 8091 / | English engineering Review, fixed clip selection, pixel evidence, Kafka and SQL verification and replay jobs. | Separate job IDs and database namespace. Preserve job deep links and engineering controls. |
| 8091 /operations | English engineering diagnostics | Preserve existing Review and Grafana links. |
| 13000 | English Grafana metrics and dashboards | Provisioning plus persistent metrics/dashboard volumes. |
| 18093 | Isolated labeled operator preview | Same operator client, separate data mount. |
| 8094 when started | Synthetic gallery-scale harness | Separate Compose project and private dataset; inactive on this VM at baseline. |

## How one T4 camera frame reaches the browser

1. `feedsimulator/publish.sh` loops a private MP4 into MediaMTX. `feed/rtsp.py` records a camera session into short retained segments. An occurrence names a particular airing, even when the video content repeats.
2. `feed/worker.py` claims that occurrence and drives `DetectionWorker` through its resident JSON command stream. `FileFeed` decodes frames at their recorded timestamps. `FrameProcessor` calls `YuNet`, then `CropStore` and `ObservationStore` persist the crop and conditional frame manifest. The manifest wins across retries. `EventPublisher` acknowledges Kafka before the worker reports a frame checkpoint.
3. The feed worker indexes the frame and its detections in SQLite. `feed/backend.py` observes SQL visibility. `identity.worker.process` reads the same occurrence and frame, computes its embedding, queries `ExactGallery` and writes the saved decision and version in the same transaction. `sightings.advance` maintains the separate camera-local appearance evidence.
4. `operator/server.py` joins that evidence for the recording timeline. `Video.tsx` selects the displayed frame by media PTS. `FaceOverlay` uses source-image coordinates and each face's detection ID. The inspector requests that ID's saved decision and crop.

## Boundaries delivered

| Caller previously coordinated | Owner after the change | Equivalence check |
|---|---|---|
| Three build contexts and the host acceptance runner depended on an unowned provisioning path. T4 service overrides lived in private files. | StarRocks DDL lives under `deploy/starrocks`. `deploy/t4` describes the VM wiring. Root Compose files still own the relative paths and project identities. | Compare normalized service/mount configurations and hash the SQL inside the feed image. |
| Frame processing constructed manifest keys/URIs, recovered a legacy timestamp and exposed Kafka's native acknowledgment. | `ObservationStore.resolveFrame` owns the stored representation and legacy recovery. `EventPublisher` returns partition and offset. The worker module declares its Jackson dependency. | `ObservationStoreTest`, `EventPublisherTest`, `tools/check_frame_events.py` and `tools/characterize_worker.py`. |
| The worker and original-frame endpoint separately selected an OpenCV backend, sought, read and released a capture. | `identity.media.Decoder` owns exact retained-frame lookup. Worker lifetime permits reuse. Each HTTP request closes its own reader. | `tools/check_retained_frames.py` compares interleaved seeks and failed-read recovery against sequential source pixels. |
| Search, gallery, camera detections and the appearance strip built deep links separately. Each player implemented its own presented-frame subscription. | `navigation.ts` owns recording links. `playback.tsx` owns frame notification, cleanup and the Persian play button. The recording player owns live following. The appearance player owns interval limits and measured boxes. | Select during playback, inspect both images, follow another observation, restore/reload the previous selection, operate by keyboard and retry a failed search at desktop and mobile widths. |

## Other entry points and review decisions

* `feed/planner.py`, `rtsp.py`, `worker.py` and `backend.py` schedule, capture, detect and confirm downstream visibility. `db.py` owns the feed schema and connection settings. `retention.py`, `throughput.py`, `soak.py`, `exporter.py` and `health.py` implement expiration, counters and service supervision. Keep retention and the frame ledger independently testable.
* `identity/api.py`, `browse.py`, `search.py` and `sighting_api.py` serve gallery, probe and evidence routes. `setup.py`, `extend.py` and `pilot.py` publish or review data. `matching.py` owns exact gallery lookup. `evaluate*.py`, `corpus.py`, `import_vidtimit.py`, `promote_vidtimit.py`, `vidtimit_*.py` and `replay_demo.py` are offline corpus, evaluation and replay entry points. Their independently computed scores provide a comparison for production lookup.
* `tools/start_*.sh` starts workspaces. `tools/operator_capacity*.py`, `operator_outage.py`, `operator_recovery.py`, `benchmark.py` and `gallery_scale.py` drive engineering trials. `tools/check_*.py`, `browser_check.py`, `acceptance.py` and Java module test sources exercise deployed boundaries. Monitoring collectors and provisioning live in `monitoring/`.
* Engineering Review's `/?run=<id>` selects a fixture run. Its frame/SQL/Kafka verification has different persisted IDs from operator evidence. The English page now links to the canonical operator shell. Existing operator `/?view=recordings&source=…&occ=…&frame=…&face=…` URLs still select the same face. Appearance return links now retain the original camera filter as well. Gallery-scale and labeled-preview media remain on their own mounts.
