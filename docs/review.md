# Review a file-feed observation

On the laptop, start the tunnel and keep its terminal open.

```sh
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:8091:127.0.0.1:8091 -L 127.0.0.1:13000:127.0.0.1:13000 ubuntu@94.183.176.83
```

Open [Review](http://127.0.0.1:8091/). Pick a saved run, go to its inference frame and click the face box. The paused image is the decoded lossless frame. During playback the browser shows a seekable WebM made from the same 20 frames. The crop request reads the private S3 object and checks its event SHA-256. Expand **Matching StarRocks row** to see the SQL row captured when the worker's Kafka offset became visible.

**New run** creates a new source and processes a single frame at the chosen feed speed. The fixed 512 × 512 clip has a labeled face in frames 0–9 and blank frames 10–19. Pick frame 15 for a verified empty manifest. Other frames in a run show “Frame not processed.” Job progress distinguishes pending work from processing errors. **Replay observation** republishes the saved observation with the same source and ID, waits for Routine Load to pass the new offset, and counts matching SQL rows. Observations contain face detection confidence. This pipeline has no recognition worker.

Review includes the API and launches a bounded CPU detector subprocess for each job. The Java job has the same worker distribution as the CLI image. Review accepts one job at a time. It has no Docker socket, shell command endpoint or connection to a public ingress. It trusts the local browser and SSH user. Do not expose it through a public proxy. Operations is at [/operations](http://127.0.0.1:8091/operations). Grafana is at [port 13000](http://127.0.0.1:13000/).

## Deploy

Run these commands at the repository root with Docker Compose v2. The host needs Python 3.10+, FFmpeg and ffprobe to prepare the reference clip. Supply a working Docker CLI for the current user. On this host the equivalent privileged command is `sudo docker compose`. Build the CPU worker first because the Review image copies its Java distribution.

```sh
python3 tools/fixture.py
export REVIEW_UID="$(id -u)" REVIEW_GID="$(id -g)"
docker compose build detector
docker compose build review
docker compose -f compose.yaml -f compose.monitoring.yaml up -d kafka s3 starrocks review prometheus grafana
```

On a fresh deployment, choose **New run** in Review. The first job provisions the database, primary-key table, Routine Load job, bucket and Kafka topic. To run the standalone CPU reference check, use the existing acceptance runner.

```sh
python3 -m venv .venv
.venv/bin/pip install -r tools/requirements-review.txt
.venv/bin/python tools/acceptance.py --speed 1
```

Compose stores Kafka data, S3 objects, StarRocks data and metadata, Prometheus samples, and Grafana data in named volumes. The host's `private/` directory holds the clip, model, reports and UI job history. Replays of an older saved run write a sidecar to `private/.review/`. First and last offsets, the saved SQL snapshot and worker logs survive an application restart. An interrupted job is marked as an error at startup. A fresh source can be started independently. The CPU reference and GPU benchmark use distinct image tags.

Run `docker compose logs --tail 100 review` for application errors. Per-job detector logs are in `private/.review/<source>.log`. Use `docker compose stop review` to release just the Review endpoint. The separate report on host port 8080 belongs to the earlier deployment.

For a repeatable browser acceptance test, install Python Playwright in the test environment and run `python tools/browser_check.py --chromium /path/to/chrome`. This creates two new sources and verifies playback, frame alignment at desktop and phone widths, SQL evidence, a replay and a blank frame.
