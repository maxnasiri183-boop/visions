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
