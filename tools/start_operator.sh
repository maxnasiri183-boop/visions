#!/usr/bin/env bash
# Start the local A100 pilot with its existing private catalog and durable run.
set -euo pipefail
cd "$(dirname "$0")/.."
case "${1:-}" in
  "") rebuild=0 ;;
  --build) rebuild=1 ;;
  *) echo "Usage: tools/start_operator.sh [--build]" >&2; exit 2 ;;
esac
export OPERATOR_DATA_HOST="${OPERATOR_DATA_HOST:-$PWD/private/operator}"
export YUNET_MODEL_HOST="${YUNET_MODEL_HOST:-$PWD/private/yunet.onnx}"
export REVIEW_UID="$(id -u)" REVIEW_GID="$(id -g)"
export OPERATOR_JAVA_IMAGE="${OPERATOR_JAVA_IMAGE:-visions-detector-gpu-operator:local}"
export FEED_KEEP_LIVE="${FEED_KEEP_LIVE:-1}" FEED_RUN_HOURS="${FEED_RUN_HOURS:-24}"
if [[ -z ${FEED_RUN_ID:-} ]]; then
  FEED_RUN_ID=$(python3 - "$OPERATOR_DATA_HOST" <<'PY'
from pathlib import Path
import sqlite3
import sys
path = Path(sys.argv[1]) / "feed.sqlite"
run = None
if path.is_file():
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        run = db.execute("SELECT run_id FROM runs WHERE state='running' ORDER BY created_at DESC LIMIT 1").fetchone()
print(run[0] if run else "operator-pilot")
PY
  )
fi
export FEED_RUN_ID
docker_cli=(docker)
if ! docker info >/dev/null 2>&1; then
  docker_cli=(sudo --preserve-env=OPERATOR_DATA_HOST,YUNET_MODEL_HOST,OPERATOR_JAVA_IMAGE,REVIEW_UID,REVIEW_GID,FEED_RUN_ID,FEED_KEEP_LIVE,FEED_RUN_HOURS,FEED_SPEED,FEED_WORKERS,FEED_RETENTION_SECONDS,FEED_QUEUE_PER_SOURCE,FEED_MIN_FREE_BYTES docker)
fi
for required in "$OPERATOR_DATA_HOST/catalog.json" "$OPERATOR_DATA_HOST/models/sface.onnx" "$YUNET_MODEL_HOST" .env; do
  if [[ ! -s "$required" ]]; then
    echo "Missing $required. Prepare the private pilot as described in docs/operator.md." >&2
    exit 1
  fi
done
base=("${docker_cli[@]}" compose --env-file .env -f compose.yaml -f compose.monitoring.yaml)
feed=("${docker_cli[@]}" compose --env-file .env -f compose.feed.yaml -f compose.feed.monitoring.yaml)
ui=("${docker_cli[@]}" compose --env-file .env -f compose.operator.yaml)
if ! "${docker_cli[@]}" image inspect visions-review:latest >/dev/null 2>&1; then
  "${base[@]}" build review
fi
"${base[@]}" up -d --wait --wait-timeout 180 kafka s3 starrocks review prometheus grafana container-monitor gpu-monitor
if ((rebuild)) || ! "${docker_cli[@]}" image inspect "$OPERATOR_JAVA_IMAGE" >/dev/null 2>&1; then
  "${docker_cli[@]}" build -f Dockerfile.gpu -t "$OPERATOR_JAVA_IMAGE" .
fi
if ((rebuild)) || ! "${docker_cli[@]}" image inspect visions-operator-worker:local visions-operator-feeder:local >/dev/null 2>&1; then
  "${feed[@]}" build operator-feeder operator-worker
fi
if ((rebuild)) || ! "${docker_cli[@]}" image inspect visions-operator:local >/dev/null 2>&1; then
  (cd operator && npm ci --no-audit --no-fund && npm run build)
  "${ui[@]}" build operator-ui
fi
"${feed[@]}" up -d --no-build --wait --wait-timeout 180 operator-feeder operator-worker operator-exporter
"${ui[@]}" up -d --no-build --wait
"${feed[@]}" up -d --no-build --wait operator-soak
