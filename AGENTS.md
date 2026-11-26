# Working on Visions

Before a deployment, compare the checkout, image revision labels and actual mounted sources. Preserve uncommitted source and keep private operator data outside Git. Run the fixed clip through detection, Kafka replay, SQL and recognition before and after changes.

* Name a module for the capability it owns. Identify one decision its caller can stop making. Review functions that combine timing, policy, serialization and retries. Extract a boundary only when it hides meaningful complexity. A concrete class is sufficient for a single implementation. See John Ousterhout, *A Philosophy of Software Design*.
* Use explicit Java imports, camelCase members and the established Java formatting. Keep persisted JSON spelling and stdout record prefixes stable, including during field renames. A Gradle library exports dependencies required by its public API. Each consumer declares its own implementation dependencies. [Java style](https://google.github.io/styleguide/javaguide.html) · [Gradle libraries](https://docs.gradle.org/current/userguide/java_library_plugin.html)
* Use PEP 8 in Python. Type cross-module inputs and outputs. Keep SQLite commit, retry and idempotency decisions together. Check actual container and CLI import paths when moving code. The deployed applications run from explicit working directories. A new distribution needs an installation test. [Python style](https://peps.python.org/pep-0008/) · [Package layouts](https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/)
* One component owns each operator interaction or selection state. Share navigation and presentation contracts across views. Keep independent evaluation oracles. [Thinking in React](https://react.dev/learn/thinking-in-react)
* A source supplies video. An occurrence identifies one airing. A frame is addressed by occurrence and index and carries media PTS. Each detected face has its own detection ID. Its observation joins geometry, crop and publication evidence. A recognition decision freezes the gallery version, winning reference and score. A person can have multiple references. An appearance groups a camera-local sequence of observations.
* Keep service configuration and storage provisioning under deployment ownership. Keep runtime clients alongside the capability that uses them. Preserve Compose projects, volumes, mounts, trust boundaries and private data on upgrades.

## Where new code belongs

- Put an executable, its build assets and its own tests in `apps/<name>/`. The operator React build and its serving API ship together. Keep Kafka, S3, SQL and filesystem clients with the application that calls them.
- Add a JVM library to `libs/` when it has a named consumer and a public contract. Register its logical name in `settings.gradle`. Install Python applications from their `pyproject.toml` and exercise them from outside the checkout.
- Put Compose files, environment wiring, provisioning and dashboards in `infra/`. Use `tools/compose` with canonical paths to anchor project and storage resolution. Keep credentials, media, models and databases outside Git.
- Put offline experiments under `research/` and cross-application commands and acceptance checks in `tools/`. Keep an existing public launcher when moving its implementation. The [component and command map](docs/packaging.md) lists the current owners.
