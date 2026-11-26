# Visions packaging

Run deployment commands with the repository's `tools/compose` launcher. It anchors project discovery, the default environment file, relative builds and host mounts at the checkout. For example, `tools/compose -f infra/compose/compose.yaml -f infra/compose/compose.monitoring.yaml config --services` lists the backend and monitoring services. Use the same private environment files and project names as the existing installation. Root Compose filenames are compatibility links for existing multi-file commands.

## Deployment ownership

| Previous location | Owner | Caller and reason |
|---|---|---|
| Root `compose*.yaml` | `infra/compose/` | Compose resolves paths against the base project. The launcher preserves its directory when selecting overlays. |
| `deploy/t4/`, `deploy/starrocks/` | `infra/t4/`, `infra/starrocks/` | T4 overrides and shared provisioning DDL are deployment inputs to the feed and engineering Review images. |
| `monitoring/` | `infra/monitoring/` | Prometheus, Grafana and the Docker collector consume tracked files. Dashboard and metrics data remain in the existing named volumes. |

See the [Docker Compose merge guide](https://docs.docker.com/compose/how-tos/multiple-compose-files/merge/).

## JVM build

The root Gradle build maps the logical projects `:detection-worker`, `:contracts` and `:vision-onnx` onto `apps/detection-worker`, `libs/contracts` and `libs/vision-onnx`. Run `gradle :detection-worker:installDist` with JDK 21 and Gradle 8.14.3, or build the Dockerfile under the application from the repository context. Use `-Pcuda=true` for ONNX Runtime GPU. The generated launcher runs `visions.worker.DetectionWorker`. The old JVM main delegates to it. Contracts expose the detection record. The inference library exposes YuNet and its ONNX Runtime exception type. The worker owns S3, Kafka and their client dependencies. Tests use the standard module `src/test/java` source sets. [Gradle multi-project builds](https://docs.gradle.org/current/userguide/multi_project_builds.html) · [Java Library plugin](https://docs.gradle.org/current/userguide/java_library_plugin.html)

## Feed and simulator

The capture and persistence services moved from `feed/*.py` into `apps/feed/src/visions_feed/`. Install `apps/feed` to run `python -m visions_feed.planner`, `.rtsp`, `.worker`, `.exporter` or `.soak` from any directory. Set `OPERATOR_DATA` for state. Outside the image, point `VISIONS_DETECTIONS_SQL` at the provisioning file in `infra/starrocks/`. Compose still invokes the original filenames through thin image launchers. The feed Dockerfile installs its own declared Python dependencies. The CPU planner needs FFmpeg. The GPU worker adds this same installation to the detector runtime. Its S3, Kafka and SQL clients stay in the feed application.

`apps/feed-simulator/publish.sh` publishes each camera using the configured FFmpeg image. Private input files remain under `feed/sample_videos` and `feed/sample_irdb`, independently of the application structure. Feed-specific tests live in `apps/feed/tests`; the old test commands under `tools/` remain valid.

## Operator application and offline research

`apps/operator/frontend` is the npm build root. Run `npm ci` and `npm run build` there before building the Python wheel or Docker image. Vite emits assets directly into `apps/operator/src/visions_operator/dist`. The installed API serves that directory. The HTTP server and recognition process share the `visions-operator` distribution, model dependencies and release image. Run `visions-operator` or `python -m identity.worker`. The Python import `operator` still belongs to the standard library.

The separately installable `research/identity` package contributes offline corpus, replay and evaluation modules to the same `identity` namespace. Install both distributions together with `python -m pip install ./apps/operator ./research/identity` or build the operator Dockerfile with `--target research` to run the existing `python -m identity.evaluate` and VidTIMIT commands. Runtime ships only the modules needed by the operator, recognition, model setup and appearance review. [PyPA package layouts](https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/)

## Engineering Review and research examples

`web/app.py`, `web/operations.py` and their static client now live together in `apps/review/src/visions_review`. Engineering Review ships HTML, CSS and JavaScript as wheel data and uses the Java launcher inside its runtime image. `visions_review.evidence` owns access to the engineering Kafka topic, S3 bucket and SQL rows. Both the API and `tools/acceptance.py` consume it. Install with `python -m pip install ./apps/review`. For a host launch, set `PRIVATE` to the prepared absolute fixture directory, `VISIONS_DETECTIONS_SQL` to the provisioning file, and configure the backend endpoints. In the image these paths are wired at build time.

`research/portraits/` owns the photo examples and their independent requirements. The public launchers `python vision.py` and `python samples.py` still work. The inspection notebook lives under `research/notebooks/` with its existing notebook path retained as a link.
