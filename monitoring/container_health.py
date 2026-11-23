"""Export this Compose project's service states into the shared telemetry directory."""
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import socket
from tempfile import NamedTemporaryFile
import time
from urllib.parse import urlencode

SERVICES = {"kafka", "s3", "starrocks", "review", "prometheus", "grafana", "gpu-monitor", "container-monitor"}
OUT = Path("/telemetry")
labels = ["com.docker.compose.project=" + os.environ["PROJECT"], "com.docker.compose.oneoff=False"]
query = "/containers/json?" + urlencode({"all": "true", "filters": json.dumps({"label": labels})})


class Engine(HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(2)
        self.sock.connect("/var/run/docker.sock")


def capture():
    conn = Engine("localhost")
    try:
        conn.request("GET", query)
        response = conn.getresponse()
        if response.status != 200:
            raise RuntimeError(f"Docker returned HTTP {response.status}")
        rows = json.load(response)
    finally:
        conn.close()
    result = {}
    for row in rows:
        service = row["Labels"].get("com.docker.compose.service")
        if service in SERVICES:
            health = (row.get("Health") or {}).get("Status")
            if not health:  # Earlier Engine releases include probe state only in Status.
                health = next((s for s in ("unhealthy", "healthy", "starting")
                               if "(" + s + ")" in row["Status"]), "none")
            result[service] = {"state": row["State"], "health": health}
    with NamedTemporaryFile(mode="w", dir=OUT, delete=False) as tmp:
        json.dump({"at": time.time(), "services": result}, tmp)
        os.fchmod(tmp.fileno(), 0o644)
    Path(tmp.name).replace(OUT / "containers.json")


if __name__ == "__main__":
    while True:
        try:
            capture()
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            print(f"Container health sampling failed: {type(exc).__name__}", flush=True)
        time.sleep(3)
