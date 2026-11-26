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
