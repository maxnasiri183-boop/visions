# Visions packaging

## What runs

The file planner or RTSP recorder creates an occurrence for each airing on a source. The feed worker hands the recording to the Java detector. The detector decodes frames, runs YuNet, stores crops and manifests in S3, and publishes keyed detection events to Kafka. StarRocks Routine Load ingests the events. The feed persists occurrence and frame information in SQLite. Recognition reads those observations and video frames, compares faces against the versioned gallery, and saves its decisions. The operator API joins that evidence to the retained media and serves the built browser client. Engineering Review has its own fixture, event trace and replay API.

| Runnable | Source and build | Launch |
|---|---|---|
| File / persistent Java detector | `apps/detection-worker/src/main/java/visions/worker`. JDK 21, Gradle 8.14.3, FFmpeg, ONNX Runtime. Run `gradle test :detection-worker:installDist` or add `-Pcuda=true` for CUDA. | `apps/detection-worker/build/install/detection-worker/bin/detection-worker serve` for the feed process. Standalone example arguments are `/input/feed.mkv demo-01 5 1.0`. Compose supplies the model path, S3 and Kafka endpoints. |
| File planner, RTSP recorder, detector supervisor | `apps/feed/src/visions_feed/{planner,rtsp,worker}.py`. Install with `python -m pip install ./apps/feed`. The feed image has a `python` target with FFmpeg and a `worker` target based on the configured Java image. | `python -m visions_feed.planner`, `python -m visions_feed.rtsp`, `python -m visions_feed.worker`. The feed overlays supply storage, the run ID and camera inputs. |
| Feed metrics, soak observer, health probe | `apps/feed/src/visions_feed/{exporter,soak,health}.py`, same package and image. | `python -m visions_feed.exporter`, `python -m visions_feed.soak`, `python -m visions_feed.health feeder`. |
| Camera publishers | `apps/feed-simulator/publish.sh` runs in the configured FFmpeg image. MediaMTX is an upstream image. | `tools/compose -f infra/compose/compose.rtsp.yaml up -d` starts the relay and both shell publishers on the prepared backend network. |
| Operator API and browser | `apps/operator/frontend` (React, TypeScript, Vite) → wheel assets in `apps/operator/src/visions_operator` (Flask, Waitress). Run `npm --prefix apps/operator/frontend ci`, `npm --prefix apps/operator/frontend run build`, then `docker build -t visions-operator:local -f apps/operator/Dockerfile .`. | `visions-operator` in the image or installed wheel. The operator overlay serves HTTP on loopback port 8092. |
| Recognition, gallery setup and appearance CLI | `apps/operator/src/identity/{worker,setup,sightings}.py`, same wheel. Uses OpenCV, NumPy, models and the private SQLite stores. | `python -m identity.worker`, `python -m identity.setup --help`, `python -m identity.sightings --help`. |
| Engineering Review and Operations | `apps/review/src/visions_review`. `python -m pip install ./apps/review` installs Flask, Waitress, evidence clients and static assets. Build the Java image before the Review image. | `visions-review` serves both `/` and `/operations` on 8091. `tools/start_workspace.sh` builds and launches the fixture workspace. |
| Data and monitoring services | `infra/compose/compose.yaml` wires the upstream Kafka, MinIO and StarRocks images. `infra/monitoring` owns Prometheus and Grafana provisioning and the container collector. | `tools/start_workspace.sh` includes monitoring. The monitoring overlay launches `python -I -B /collector.py` and an NVIDIA metrics shell collector on the GPU profile. |
| Identity research and replay | `research/identity/src/identity`. Install with `python -m pip install ./apps/operator ./research/identity`, or build the operator image with `--target research` after the frontend build. | `python -m identity.<job>` for `corpus`, `pilot`, `extend`, `evaluate`, `evaluate_sightings`, `import_vidtimit`, `promote_vidtimit`, `vidtimit_demo_check`, `vidtimit_live_check`, `replay_demo`. The candidate and validation modules support those jobs. |
| Photo examples and inspection notebook | `research/portraits`, `research/notebooks`. Install `python -m pip install -r research/portraits/requirements.txt`. Jupyter is optional. | `python samples.py huggingface`, `python vision.py detect IMAGE`, or copy the inspection notebook to the prepared private directory and open it in Jupyter. [Notebook walkthrough](first-feed.fa.md) |
| Scale workspace and engineering harnesses | `tools/gallery_scale.py` uses the operator and feed code. Its `--code` option explicitly selects a checkout to compare. `tools/` also owns fixture, acceptance, benchmark, capacity/recovery, release and browser commands. | `python tools/gallery_scale.py fixture --size 100000` prepares a private synthetic fixture. `serve` runs it. The gallery-scale Compose overlay serves the operator image against its own data. See the [scale guide](gallery-scale.md) and [capacity guide](operator-capacity.md). |

Use the repository root as the working directory for the relative build commands above. Open a local virtual environment before installing Python packages. For an installed operator UI, build the frontend before installing the wheel. The feed requires Python 3.11 or later. The images pin Python 3.12 for the operator and feed. Engineering tools that invoke Docker need the socket or CLI access described in their runbooks.

### Engineering commands

Run the following Python scripts as `python tools/<name>.py`. The feed and operator test links resolve to their application-owned tests.

| Purpose | Commands and runtime |
|---|---|
| Engineering fixture and Java event stream | `fixture` prepares the pinned clip and model. `acceptance`, `characterize_worker`, `check_frame_events` and `benchmark` exercise the Java worker and evidence services. `browser_check` drives the Review HTTP service using Playwright. |
| Feed regression | `check_feed_contention`, `check_operator_catalog`, `check_rtsp_retention`, `check_throughput_ledger` exercise the installed feed on disposable stores. `check_operator_throughput` runs against the exporter’s Kafka/SQL environment. |
| Operator evidence | `check_identity_first` uses a temporary fixture. `check_retained_frames` and `check_operator_decoder` read retained media. `check_gallery_replay`, `check_operator_browser` and `check_video_workspace` drive the operator HTTP UI with Playwright. `pin_review_example` preserves a chosen private example. |
| Capacity, recovery and scale | `operator_capacity` orchestrates a separate QA project, with `operator_capacity_report` as its reporting library and `operator_capacity_expire` for QA cleanup. `operator_outage` and `operator_recovery` exercise interruption on a selected trial. `gallery_scale` owns synthetic gallery generation and measurements. |
| Packaging and deployment | `check_build_contexts` and `check_compose_migration` use BuildKit and Compose. The shell entry points are `tools/compose`, `tools/start_workspace.sh`, `tools/start_operator.sh` and `tools/check_deployed_revision.sh`. |

## Ownership and path migration

```text
apps/
  detection-worker/         Java application, tests, Gradle and CPU/CUDA Dockerfiles
  feed/                     Python distribution, job loops, feed adapters and tests
  feed-simulator/           FFmpeg RTSP publishers
  operator/                 frontend/, src/{visions_operator,identity}/, tests/
  review/                   engineering API, evidence clients and static browser
libs/{contracts,vision-onnx}/  Gradle libraries with standard Java source sets
infra/{compose,t4,starrocks,monitoring}/
research/{identity,portraits,notebooks}/
tools/                      deployment launchers and cross-application checks
docs/                       operator, engineering and deployment runbooks
```

| Previous path or caller | Current owner and reason |
|---|---|
| `compose*.yaml`, `deploy/t4`, `deploy/starrocks`, `monitoring` | `infra/compose`, `infra/t4`, `infra/starrocks`, `infra/monitoring`. Root and old deployment paths are compatibility links to the authoritative files. |
| `detection-worker`, root `Dockerfile{,.gpu}` | `apps/detection-worker` owns the executable and both images. External callers can still use the root Dockerfile paths and Gradle project name. |
| `contracts`, `vision-onnx` | `libs/contracts`, `libs/vision-onnx`. The worker consumes the detection record and the YuNet inference API. Logical Gradle names and public library types are stable. |
| `feed/*.py`, `feedsimulator`, feed-only checks in `tools` | `apps/feed/src/visions_feed`, `apps/feed-simulator`, `apps/feed/tests`. Existing container job filenames and test commands are supported. The private `feed/sample_videos` and `feed/sample_irdb` input paths retain their original ownership. |
| `operator` (React project, Python script and Dockerfile) | `apps/operator/frontend` and `apps/operator/src/visions_operator`. The npm build root, Python package name and destination for assets are explicit. The image supports the old server script invocation. |
| `identity/*.py` | `apps/operator/src/identity` owns runtime recognition. `research/identity/src/identity` owns optional experiment commands. Each wheel contributes distinct modules to the existing import namespace. |
| `web`, evidence-client functions in `tools/acceptance.py` | `apps/review/src/visions_review`. Static files and HTTP routes now build together. `visions_review.evidence` supplies both the server and the acceptance runner. |
| Root photo examples, requirements and `notebooks/` | `research/portraits` and `research/notebooks` own the examples. Existing root example commands, requirements and notebook link remain available. |

## Packaging review

| Affected caller and original problem | Correction and verification |
|---|---|
| Root Compose definitions, `tools/operator_capacity.py:run_level` and `tools/operator_capacity_expire.py:expire`. Moving the base file or dereferencing its link changes the base directory for builds, mounts and unnamed projects. | The launcher and capacity harnesses pass an explicit project directory. The migration check compares the full resolved models against the old revision, through both supported paths. |
| `feed/Dockerfile`, planner/worker imports. T4 feed inherited the Review application environment. Sibling-module imports required files to be in the current interpreter search path. | The feed wheel declares its own boto3, botocore, kafka-python and PyMySQL dependencies. The CPU and GPU targets install the same package and image compatibility launchers call its modules. Exercise planner restart and retention using the packaged interpreter from another directory. |
| `detection-worker/build.gradle`, `DetectionWorker` and `CropStore`. Worker implementation shared the library package and compiled against direct AWS imports through the S3 dependency. | Move implementation to `visions.worker`, retain the forwarding main, declare the used SDK modules and run the generated launcher against the fixed recording. Keep ONNX Runtime exported because a public YuNet signature exposes its type. |
| `operator/package.json`, `operator/server.py` and private Compose overrides. Bundled assets and Python source had separate path assumptions. Naming an installed top-level package `operator` would shadow the standard library. | Vite emits into `visions_operator/dist`. The wheel includes the production files and the image checks for the entry HTML. Verify the installed resources, standard-library import and a bookmarked frame/face in a browser. |
| The operator image copied all of `identity/`, including corpus import and scale evaluation. Research jobs and the live recognition worker shared a release dependency. | Keep live modules in the operator wheel and install the optional research wheel only for its commands. Exercise runtime imports and evaluation with the two wheels installed together. |
| `web/app.py` imported helpers from a host acceptance script via `PYTHONPATH`. The HTTP image needed source outside its own application. | Move the shared evidence reads behind the Review package boundary. Run the installed server and acceptance CLI from outside the checkout, and request the packaged static files. |
| `tools/gallery_scale.py` loaded a server file and feed schema by their old physical paths. The capacity harness forced the repository root into subprocess import lookup. | The scale harness explicitly locates either supported layout for its chosen revision. The capacity harness imports the installed operator distribution. Check the scale fixture and installed recognition CLI in a disposable environment. |
| JVM `COPY . .` and the app-specific ignore files. A parent-directory exception let a populated sibling frontend dependency tree enter the Java build context. | Allow individual manifests and source paths. `tools/check_build_contexts.py` exports an entire synthetic context for each filter and checks the exact visible file set, including private-data and sibling-app exclusions. |
| `identity.corpus` and `identity.vidtimit_candidate` shell out to FFmpeg and ffprobe. Both commands were missing from the original slim operator image. | Install Debian’s FFmpeg package in the research target and document the host dependency. Check clip generation and packet inspection in that environment. |

The repository contains no tracked CI workflow. The Dockerfile-specific context allowlists, root Gradle project mapping, package manifests and Compose overlays are the executable build and deployment configuration.

## How to deploy

Use `tools/start_workspace.sh` for CPU engineering Review, `--gpu` for the GPU benchmark stack, or `--t4` for the single-GPU Review stack. The file-feed operator uses `tools/start_operator.sh`. The T4 RTSP deployment uses the backend, simulator, feed and operator projects with their private environment files and pinned images. The scale workspace has an independent Compose project, data mount and loopback port 8094. [RTSP deployment commands](../apps/feed-simulator/README.md) · [Operator startup](operator.md) · [Engineering workspace](review.md)

Use the repository’s `tools/compose` launcher with canonical Compose paths. It changes to the checkout and supplies the project directory, so the following command also works when the launcher is invoked by absolute path from another directory.

```sh
tools/compose -f infra/compose/compose.yaml -f infra/compose/compose.monitoring.yaml config --services
```

Existing `docker compose -f compose.yaml ...` commands use the root compatibility links. When invoking Docker directly with canonical infra filenames, pass `--project-directory` pointing at the checkout. Otherwise Compose resolves paths relative to `infra/compose`. Use the same project names and private env files as the installation. The existing volume names, data and model locations depend on that context. [Compose path resolution](https://docs.docker.com/compose/how-tos/multiple-compose-files/merge/)

The worker Dockerfiles use the repository build context to compile the named Gradle projects. The root ignore file admits their sources and build configuration. The Python Dockerfiles have narrower per-Dockerfile allowlists. Install `apps/operator` after the frontend build when serving its web UI. For host launches, set `OPERATOR_DATA` (feed), `OPERATOR_ROOT` (identity), and `PRIVATE` (Review) to the prepared absolute directories. Set `VISIONS_DETECTIONS_SQL` to the provisioning file under `infra/starrocks/` for host Review or feed clients, and configure their service endpoints. The container images supply their runtime paths. [Docker build contexts](https://docs.docker.com/build/concepts/context/) · [Gradle multi-project builds](https://docs.gradle.org/current/userguide/multi_project_builds.html) · [Gradle Java Library](https://docs.gradle.org/current/userguide/java_library_plugin.html) · [PyPA source layouts](https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/)

## Verification

- `python3 tools/check_compose_migration.py` compares the full normalized service models for 15 stack combinations against the pre-migration Git revision, using temporary test inputs. It checks the root compatibility paths and canonical launcher, including startup-script overlay order and profiles. On the running installation a separate read-only comparison checked 7 site configurations for 17 services, including all 6 declared named-volume mounts and 24 binds. Kafka also has two image-defined mounts. An independent preview on loopback port 18093 uses the same operator image with a separate data bind and the legacy server command.
- `python3 tools/check_build_contexts.py` exported four BuildKit contexts and checked every file against the expected set. The real GPU Gradle build also passed with the final JVM context filter.
- A clean archive of tracked source built with Gradle 8.14.3 and JDK 21. Both `gradle test :detection-worker:installDist` and `gradle --offline -Pcuda=true test :detection-worker:installDist` executed 12 tasks. The candidate launcher and its three first-party jars ran in the existing T4 native runtime. Against the fixed clip, the fresh and replayed requests each processed 11 frames (one face, ten blank). The fresh frame matched the baseline geometry, score, landmarks, PTS and crop digest. Replay kept the event, detection ID and one logical SQL row while advancing the Kafka offset.
- `npm ci --offline --no-audit --no-fund` and `npm run build` ran in the clean frontend directory. The production bundles matched the original hashes. `pip wheel --no-deps` built the four Python distributions. They installed together, `pip check` passed, and `python -I` from `/tmp` resolved their modules and assets outside the checkout. The recognition and research evaluation CLIs returned their help.
- The packaged feed ran `check_feed_contention.py`, `check_operator_catalog.py`, `check_rtsp_retention.py` and `check_throughput_ledger.py` on isolated state. `tools/check_identity_first.py` passed in the packaged operator environment. The Review server returned its health, HTML, Operations, JavaScript and saved runs from a prepared fixture.
- A headless Firefox browser opened the same saved operator URL before and after the move. The occurrence, selected frame, active face box and evidence ID matched, the crop loaded, and the video decoded. The saved gallery thumbnail and recording had identical response digests. A separate fresh operator Compose project created its own empty named volume, started healthy, returned an empty gallery, and was removed. Existing backend storage stayed attached during these checks.
- `python tools/gallery_scale.py fixture --size 100000` and `python tools/gallery_scale.py plans --size 100000` ran in a temporary memory-backed store. The generated dataset held 100,000 distinct people and 129,412 references. All query-plan assertions passed.
- The optional research wheel and its Debian media dependency ran on a memory-backed filesystem. FFmpeg generated a fixture clip and ffprobe counted all three packets. The full research image export after adding FFmpeg and a full native GPU image export were stopped when the VM's existing recording retention entered disk-pressure cleanup. Native image builds and retained footage share capacity on this installation. Use a separate build disk or builder when repeating them.
- T4 inference used the candidate executable in the installed native runtime. The CPU and CUDA Gradle distributions built from tracked source. The A100 deployment, the complete CPU service stack and a fresh backend cluster received configuration checks. The fresh operator startup and the existing backend mounts received runtime checks.
