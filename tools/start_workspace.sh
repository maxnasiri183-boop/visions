#!/usr/bin/env bash
# Build the worker and start the localhost workspace from any directory.
set -euo pipefail
cd "$(dirname "$0")/.."
case "${1:-}" in
  "") gpu=(); overlay=() ;;
  --gpu) gpu=(gpu-monitor); overlay=(-f compose.gpu.yaml) ;;
  *) echo "Usage: tools/start_workspace.sh [--gpu]" >&2; exit 2 ;;
esac
docker_cli=(docker)
if ! docker info >/dev/null 2>&1; then
  docker_cli=(sudo docker)
fi
if [[ ! -f private/feed.mkv || ! -f private/yunet.onnx || ! -f .env ]]; then
  python3 tools/fixture.py
fi
mkdir -p private/monitor
chmod g+w private/monitor
# Pass UID/GID even when sudo resets the calling environment.
compose() {
  "${docker_cli[@]}" compose --env-file .env -f compose.yaml -f compose.monitoring.yaml "${overlay[@]}" "$@"
}
export REVIEW_UID="$(id -u)" REVIEW_GID="$(id -g)"
if [[ ${docker_cli[0]} == sudo ]]; then
  docker_cli=(sudo --preserve-env=REVIEW_UID,REVIEW_GID docker)
fi
compose build detector
compose build review
if (( ${#gpu[@]} )); then
  compose build detector-gpu
fi
compose up -d --wait --wait-timeout 180 kafka s3 starrocks review prometheus grafana container-monitor "${gpu[@]}"
