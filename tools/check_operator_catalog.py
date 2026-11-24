"""Regression check: a removed, expired asset can return to the same running planner."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.environ["OPERATOR_DATA"] = directory
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feed"))
        from backend import prune_media
        from db import connect, initialize
        from planner import read_catalog

        path = root / "input.mkv"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=black:size=64x64:rate=4",
                "-frames:v",
                "4",
                "-c:v",
                "ffv1",
                str(path),
            ],
            check=True,
        )
        entry = {
            "asset_id": "fixture",
            "session_id": "qa",
            "source_id": "qa",
            "path": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "synthetic": True,
        }
        catalog = root / "catalog.json"
        initialize()
        db = connect()
        catalog.write_text(json.dumps([entry]))
        cache = {}
        first = read_catalog(db, cache)["qa"][0][1]
        catalog.write_text("[]")
        assert not read_catalog(db, cache)
        prune_media(db)
        assert not (root / first["input_path"]).exists()
        assert path.is_file()
        catalog.write_text(json.dumps([entry]))
        again = read_catalog(db, cache)["qa"][0][1]
        assert (root / again["input_path"]).is_file() and (
            root / again["media_path"]
        ).is_file()
        assert again["checksum"] == first["checksum"]
        missing = dict(entry, asset_id="missing", path="missing.mkv")
        catalog.write_text(json.dumps([missing, entry]))
        assert not read_catalog(db, cache)
        assert (
            db.execute(
                "SELECT state FROM service_state WHERE service='source:qa'"
            ).fetchone()[0]
            == "source_outage"
        )
        catalog.write_text(json.dumps([entry]))
        assert "qa" in read_catalog(db, cache)
        print("PASS reintroduced catalog source and partial playlist outage")
