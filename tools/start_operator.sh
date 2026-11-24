#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
(cd operator && npm ci --no-audit --no-fund && npm run build)
docker compose -f compose.operator.yaml build operator-ui
docker compose -f compose.operator.yaml up -d --no-build --wait
