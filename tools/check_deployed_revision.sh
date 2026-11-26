#!/usr/bin/env bash
# Check the operator's first-party images, browser bundle and provisioned rate charts.
set -euo pipefail
cd "$(dirname "$0")/.."
revision=${1:-$(git rev-parse HEAD)}
docker_cli=(docker)
if ! docker info >/dev/null 2>&1; then docker_cli=(sudo -n docker); fi
for pair in visions-operator/recognition visions-operator/operator-ui \
  visions-operator-feed/operator-worker visions-operator-feed/operator-feeder \
  visions-operator-feed/operator-exporter visions-operator-feed/operator-soak; do
  container=$("${docker_cli[@]}" ps -q --filter "label=com.docker.compose.project=${pair%/*}" --filter "label=com.docker.compose.service=${pair#*/}")
  image=$("${docker_cli[@]}" inspect "$container" --format '{{.Image}}')
  actual=$("${docker_cli[@]}" image inspect "$image" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
  if [[ "$actual" != "$revision" ]]; then echo "$pair serves $actual, expected $revision" >&2; exit 1; fi
  echo "$pair $actual"
done
python3 - "$revision" <<'PY'
from urllib.request import urlopen
from pathlib import Path
import json,re,sys
ui='http://127.0.0.1:8092'
assert json.load(urlopen(ui+'/health',timeout=5))['revision']==sys.argv[1]
assets=re.findall(r'"(/assets/[^"]+)"',urlopen(ui,timeout=5).read().decode())
assert len(assets)>=2
for asset in assets:
    assert urlopen(ui+asset,timeout=5).read()==(Path('apps/operator/src/visions_operator/dist') / asset.lstrip('/')).read_bytes(),asset
dash=json.load(urlopen('http://127.0.0.1:13000/api/dashboards/uid/visions-operator',timeout=5))['dashboard']
assert [p['title'] for p in dash['panels'][:2]]==['Frames per second','Face observations per second']
print('PASS · served operator bundle, health revision and provisioned rate panels')
PY
