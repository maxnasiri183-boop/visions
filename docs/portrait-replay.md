# Portrait to video, feed rate and SQL evidence

The Persian operator, the engineering dashboards and the engineering fixture bind to loopback. Keep this SSH session open on the laptop. The address below is the project owner's SSH destination.

```bash
ssh -N -o ExitOnForwardFailure=yes \
  -L 8092:127.0.0.1:8092 -L 8091:127.0.0.1:8091 \
  -L 13000:127.0.0.1:13000 -L 8094:127.0.0.1:8094 ubuntu@94.183.176.83
```

1. Open the [real reference gallery](http://127.0.0.1:8092/?view=gallery). Select a person with a saved video match. Open **باز کردن ویدئو** below an enrollment portrait. The links use the selected enrollment's actual saved decisions and version when specified.
2. Select the face and press **مقایسه با گالری**. This retrieves the saved worker decision. Under **جزئیات فنی**, inspect the original frame, cosine score, threshold, model and gallery version. The SQL response and Kafka offsets reference the same detection ID. **محاسبهٔ مجدد شباهت دو تصویر** computes a new pairwise cosine from the stored model-compatible observation and enrollment vectors. It leaves the original decision intact. The original VidTIMIT recording dates were unpublished. The operator distinguishes the session ID, recording PTS, airing time and observed wall clock.
3. Open [the live replay source](http://127.0.0.1:8092/?view=live&source=replay-lab) and the [rate dashboard](http://127.0.0.1:13000/d/visions-operator/operator-feed-and-search?from=now-15m&to=now&refresh=5s). Select **سرعت ارسال منبع** at 0.5×. After roughly 90 s select 1×. The player speed inside the video controls affects laptop playback. The source-speed control affects the actual detector input. An assigned clip completes at its original speed. The planner starts the new speed at a fresh wall-clock boundary. Check both time series, the target and the selected-period count. The selector defaults to the last durable setting after a restart.
4. Browse [the isolated 300,000-person engineering fixture](http://127.0.0.1:8094/?view=gallery), search for `scale-230900`, open its record, then go back. This port serves synthetic people and geometric thumbnail tiles. The real gallery on 8092 remains available throughout.

## Prepare and deploy

From this checkout on the A100, retain the existing private directory, Compose project names, private environment file and named volumes. Prepare the original research pilot first on a new installation. [Baseline preparation](../research/identity/README.md)

```bash
cd /home/ubuntu/visions
.venv/bin/python -m identity.replay_demo --root private/operator
.venv/bin/python tools/gallery_scale.py fixture --size 300000
tools/start_operator.sh --build
sudo docker compose -f compose.gallery-scale.yaml up -d --no-build --wait
tools/check_deployed_revision.sh "$(git rev-parse origin/main)"
```

Each deliberate airing has a fresh occurrence ID unique to the durable run, source and playlist sequence. Retrying that airing resumes its checkpoint and uses the same detection IDs and saved Kafka payloads; an acknowledged republication receives a later broker offset. StarRocks stores a single logical row per ID. The primary study source rotates its playlist, and the repeatable throughput source alternates the two moving faces and an empty interval in the same six-second clip. Both default to 1× in a new configuration. The demo includes a labeled spatial composition from two independent session-2 recordings. Inspect a solo recording to see a single original camera view.

The private dataset import, operating points, throughput reconciliation and engineering scale procedures each have a separate runbook. [Dataset import and quality](vidtimit43.md) · [Throughput](throughput.md) · [Gallery load test](gallery-scale.md) · [Larger real datasets](large-gallery-data.md)

Before replacing a deployment record its current SHA and retain its Docker images. If the application verification fails after a release, restore the last verified checkout, rebuild the same services against the same private directories and repeat the browser and SQL checks. Persistent volumes must stay mounted throughout recovery.

## Deployment baseline for this milestone

Before implementation the A100 server was running the feature stack through PR #14 at `e936d4f`, while GitHub `main` pointed to `6e014c5`. The retained, fixed two-face recording passed the browser suite on that original deployment. The server is Ubuntu 24.04.2 LTS with one NVIDIA A100-SXM4-40GB (40,960 MiB, driver 595.99.02). Docker reported three running Compose projects. Kafka, object storage, StarRocks, Prometheus and Grafana used existing Docker volumes. Feed and recognition checkpoints, media and references used the private operator bind mount. The 145 GiB filesystem initially had 76 GiB available. The unrelated `public-report/` directory was present before the milestone.

The gallery defaults to its active published membership. Use the version selector for an older snapshot or the explicit draft, which also includes rejected enrollments. Historical person links resolve the last version containing that identity. Versioned on-demand comparisons require the original pair of compatible embeddings. All private subject crosswalks and biometric evidence stay outside Git.
