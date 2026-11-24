"""Record a bounded interruption in a private source planner; recovery is automatic."""

import argparse
import json
import tempfile
import time
from pathlib import Path

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--seconds", type=int, default=15)
    a = p.parse_args()
    if a.seconds <= 0 or a.seconds > 300:
        p.error("Use a positive interval up to five minutes")
    catalog = json.loads((a.data / "catalog.json").read_text())
    if a.source not in {i["source_id"] for i in catalog}:
        p.error("Source is absent from catalog")
    path = a.data / "outages.json"
    payload = json.loads(path.read_text()) if path.exists() else {}
    payload[a.source] = time.time() + a.seconds
    with tempfile.NamedTemporaryFile(mode="w", dir=a.data, delete=False) as f:
        json.dump(payload, f)
    Path(f.name).replace(path)
    print(f"Source {a.source} paused for {a.seconds} seconds", flush=True)
