"""Exercise transient SQLite writer contention and the resident worker's fatal exit."""

import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch


FEED = Path(__file__).resolve().parents[1] / "feed"
sys.path.insert(0, str(FEED))


def check_supervisor():
    import worker

    started = threading.Event()
    stopped = threading.Event()

    def waiting_slot(_):
        started.set()
        worker.STOP.wait()
        stopped.set()

    def bad_state(*_):
        assert started.wait(2)
        raise RuntimeError("test fatal supervisor error")

    with (
        patch.object(worker, "slot", waiting_slot),
        patch.object(worker, "state", bad_state),
    ):
        try:
            worker.supervise(None, 1)
        except RuntimeError as error:
            assert str(error) == "test fatal supervisor error"
        else:
            raise AssertionError("Supervisor concealed its fatal error")
    assert stopped.is_set()


def check_locks(root):
    os.environ["OPERATOR_DATA"] = str(root)
    from db import connect, state

    feed = connect()
    feed.executescript(
        """PRAGMA journal_mode=WAL;
        CREATE TABLE observations(detection_id TEXT PRIMARY KEY, search_visible_at REAL, search_state TEXT);
        CREATE TABLE service_state(service TEXT PRIMARY KEY, heartbeat_at REAL, state TEXT, detail TEXT);
        CREATE TABLE notices(at REAL, service TEXT, state TEXT, detail TEXT);
        INSERT INTO observations VALUES ('synthetic-id',NULL,NULL);"""
    )
    writer = connect()
    writer.execute("BEGIN IMMEDIATE")
    try:
        feed.execute("PRAGMA busy_timeout=35")
        state(feed, "worker", "active")
        assert not feed.execute("SELECT * FROM service_state").fetchall()
    finally:
        writer.rollback()
        writer.close()
    state(feed, "worker", "active")
    assert (
        feed.execute(
            "SELECT state FROM service_state WHERE service='worker'"
        ).fetchone()[0]
        == "active"
    )

    identity = sqlite3.connect(root / "identity.sqlite", isolation_level=None)
    identity.executescript(
        """PRAGMA journal_mode=WAL;
        CREATE TABLE indexed(detection_id TEXT PRIMARY KEY,state TEXT,embedding BLOB);
        INSERT INTO indexed VALUES ('synthetic-id','ready',X'0102');"""
    )
    identity.execute("BEGIN IMMEDIATE")
    identity.execute("INSERT INTO indexed VALUES ('uncommitted','ready',X'0103')")
    start = time.monotonic()
    observer = subprocess.Popen(
        [sys.executable, "-c", "import backend; backend.search_visibility_loop()"],
        cwd=FEED,
    )
    try:
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            ready = feed.execute(
                "SELECT search_visible_at FROM observations WHERE detection_id='synthetic-id'"
            ).fetchone()[0]
            if ready is not None:
                break
            time.sleep(0.03)
        else:
            raise AssertionError(
                "A writer on the attached identity index stalled feed visibility"
            )
        assert identity.in_transaction
        print(
            f"PASS observed committed identity in {time.monotonic() - start:.3f}s while identity writer stayed locked"
        )
    finally:
        observer.terminate()
        observer.wait(timeout=4)
        identity.rollback()
        identity.close()
        feed.close()


if __name__ == "__main__":
    if sys.argv[1:] == ["supervisor"]:
        check_supervisor()
    else:
        with tempfile.TemporaryDirectory() as temp:
            check_locks(Path(temp))
        subprocess.run([sys.executable, __file__, "supervisor"], check=True, timeout=5)
        print("PASS busy heartbeat recovery and fatal supervisor shutdown")
