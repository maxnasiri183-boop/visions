"""Regression check: a removed, expired asset can return to the same running planner."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def wait_for(operation, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = operation()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("Timed out waiting for catalog state")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.environ["OPERATOR_DATA"] = directory
        from visions_feed.backend import prune_media
        from visions_feed.db import connect, initialize
        from visions_feed.planner import Catalog

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
        reader = Catalog()
        first = wait_for(lambda: reader.read(db).get("qa"))[0][1]
        catalog.write_text("[]")
        assert not reader.read(db)
        prune_media(db)
        assert not (root / first["input_path"]).exists()
        assert path.is_file()
        catalog.write_text(json.dumps([entry]))
        again = wait_for(lambda: reader.read(db).get("qa"))[0][1]
        assert (root / again["input_path"]).is_file() and (
            root / again["media_path"]
        ).is_file()
        assert again["checksum"] == first["checksum"]
        missing = dict(entry, asset_id="missing", path="missing.mkv")
        catalog.write_text(json.dumps([missing, entry]))
        assert not reader.read(db)
        assert (
            db.execute(
                "SELECT state FROM service_state WHERE service='source:qa'"
            ).fetchone()[0]
            == "source_outage"
        )
        catalog.write_text(json.dumps([entry]))
        assert "qa" in reader.read(db)
        reader.pool.shutdown()
        staged = root / "new.mkv"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=red:size=64x64:rate=4",
                "-frames:v",
                "4",
                "-c:v",
                "ffv1",
                str(staged),
            ],
            check=True,
        )
        new_digest = hashlib.sha256(staged.read_bytes()).hexdigest()
        extra = dict(entry, asset_id="new", path=staged.name, sha256=new_digest)
        # Hold the actual transcode for the newly added clip while the planner is running.
        # An unchanged second camera and the camera receiving new media must both advance.
        binary = shutil.which("ffmpeg")
        wrapper = root / "bin"
        wrapper.mkdir()
        helper = wrapper / "ffmpeg"
        helper.write_text(f"""#!{sys.executable}
import os, pathlib, sys, time
if any({new_digest!r} in arg for arg in sys.argv):
    pathlib.Path({str(root / "building")!r}).touch()
    while not pathlib.Path({str(root / "release")!r}).exists():
        time.sleep(0.05)
os.execv({binary!r},[{binary!r},*sys.argv[1:]])
""")
        helper.chmod(0o700)
        standby = dict(entry, source_id="second-camera")
        catalog.write_text(json.dumps([entry, standby]))
        env = dict(
            os.environ,
            PATH=str(wrapper) + os.pathsep + os.environ["PATH"],
            FEED_RUN_ID="catalog-promotion-check",
            FEED_SPEED="2",
            FEED_QUEUE_PER_SOURCE="24",
        )
        planner = subprocess.Popen([sys.executable, "-m", "visions_feed.planner"], env=env)

        def sequences():
            return dict(
                db.execute(
                    "SELECT source_id,max(sequence) FROM occurrences GROUP BY source_id"
                )
            )

        try:
            wait_for(lambda: len(sequences()) == 2)
            catalog.write_text(json.dumps([extra, entry, standby]))
            wait_for((root / "building").exists)
            before = sequences()
            wait_for(
                lambda: all(
                    sequences().get(cam, -1) >= seq + 2 for cam, seq in before.items()
                ),
                timeout=5,
            )
            assert (
                db.execute(
                    "SELECT count(*) FROM occurrences WHERE asset_id='new'"
                ).fetchone()[0]
                == 0
            )
            assert (
                db.execute(
                    "SELECT state FROM service_state WHERE service='catalog-preparation'"
                ).fetchone()[0]
                == "preparing"
            )
            assert (
                db.execute(
                    "SELECT state FROM service_state WHERE service='source:qa'"
                ).fetchone()[0]
                == "online"
            )
            (root / "release").touch()
            wait_for(
                lambda: db.execute(
                    "SELECT count(*) FROM occurrences WHERE asset_id='new'"
                ).fetchone()[0]
                > 0
            )
            row = db.execute(
                "SELECT input_path,media_path,frame_count FROM occurrences WHERE asset_id='new'"
            ).fetchone()
            assert (
                (root / row[0]).is_file() and (root / row[1]).is_file() and row[2] == 4
            )
        finally:
            (root / "release").touch()
            planner.terminate()
            planner.wait(timeout=10)
        # Restart on the same DB and verify that the admission sequence continues.
        last = sequences()
        planner = subprocess.Popen([sys.executable, "-m", "visions_feed.planner"], env=env)
        try:
            wait_for(
                lambda: all(
                    sequences().get(cam, -1) > seq for cam, seq in last.items()
                ),
                timeout=6,
            )
            for camera, high in sequences().items():
                assert (
                    db.execute(
                        "SELECT count(*) FROM occurrences WHERE source_id=?", (camera,)
                    ).fetchone()[0]
                    == high + 1
                )
        finally:
            planner.terminate()
            planner.wait(timeout=10)
        print(
            "PASS reintroduced media, partial outage, nonblocking live catalog promotion and planner restart"
        )
