#!/usr/bin/env bash
# Build the worker and start the localhost workspace from any directory.
set -euo pipefail
cd "$(dirname "$0")/.."
overlay=(-f compose.monitoring.yaml)
services=(kafka s3 starrocks review)
workers=(detector)
case "${1:-}" in
  "") services+=(prometheus grafana container-monitor) ;;
  --gpu)
    overlay+=(-f compose.gpu.yaml)
    services+=(prometheus grafana container-monitor gpu-monitor)
    workers+=(detector-gpu)
    ;;
  --t4)
    overlay=(-f compose.gpu.yaml -f compose.t4.yaml)
    workers=(detector-gpu)
    ;;
  *) echo "Usage: tools/start_workspace.sh [--gpu|--t4]" >&2; exit 2 ;;
esac
docker_cli=(docker)
if ! docker_error=$(docker info --format '{{.ServerVersion}}' 2>&1); then
  printf '%s\n' "$docker_error" >&2
  if ! sudo docker info --format '{{.ServerVersion}}' >/dev/null; then
    echo "Start this workspace from a VM terminal where docker info succeeds." >&2
    exit 1
  fi
  docker_cli=(sudo docker)
fi
if [[ ${1:-} == --t4 ]]; then
  "${docker_cli[@]}" run --rm --device=nvidia.com/gpu=0 ubuntu:24.04 nvidia-smi -L
fi
if [[ ! -f private/feed.mkv || ! -f private/yunet.onnx || ! -f .env ]]; then
  if [[ ${1:-} == --t4 ]]; then
    python3 tools/fixture.py --prepare-only
  else
    python3 tools/fixture.py
  fi
fi
mkdir -p private/monitor
chmod g+w private/monitor
# Pass UID/GID even when sudo resets the calling environment.
compose() {
  "${docker_cli[@]}" compose --env-file .env -f compose.yaml "${overlay[@]}" "$@"
}
export REVIEW_UID="$(id -u)" REVIEW_GID="$(id -g)"
if [[ ${docker_cli[0]} == sudo ]]; then
  docker_cli=(sudo --preserve-env=REVIEW_UID,REVIEW_GID docker)
fi
# Review copies its Java worker from the completed local image.
for worker in "${workers[@]}"; do
  compose build "$worker"
done
compose build review
if [[ ${1:-} == --t4 && ! -f private/feed.mkv ]]; then
  compose run --rm --no-deps -T --entrypoint python \
    -v "$PWD/tools/fixture.py:/app/tools/fixture.py:ro" -v "$PWD/.env:/app/.env:ro" \
    review tools/fixture.py
fi
compose up -d --wait --wait-timeout 180 "${services[@]}"
