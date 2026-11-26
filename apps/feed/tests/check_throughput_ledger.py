"""Check SQL deduplication and Kafka checkpoint recovery using an isolated SQLite file."""

from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from visions_feed.throughput import account_records, install, start_cursor


def verify(path):
    db = sqlite3.connect(path, isolation_level=None)
    db.executescript("""
        CREATE TABLE occurrences(occurrence_id TEXT PRIMARY KEY,run_id TEXT,source_id TEXT);
        CREATE TABLE observations(detection_id TEXT PRIMARY KEY,occurrence_id TEXT,sql_visible_at REAL);
        CREATE TABLE expiry_counts(occurrence_id TEXT PRIMARY KEY,detections INT,missing_sql INT);
        INSERT INTO occurrences VALUES('live','run','camera'),('expired','run','camera');
        INSERT INTO expiry_counts VALUES('expired',5,1);
        INSERT INTO observations VALUES('id','live',NULL),('retained','live',3);
    """)
    install(db)
    assert db.execute("SELECT sql_visible FROM throughput_source").fetchone() == (5,)
    db.execute("BEGIN")
    db.execute("UPDATE observations SET sql_visible_at=4 WHERE detection_id='id'")
    db.rollback()
    assert db.execute("SELECT sql_visible FROM throughput_source").fetchone() == (5,)
    db.execute("UPDATE observations SET sql_visible_at=5 WHERE detection_id='id'")
    db.execute("UPDATE observations SET sql_visible_at=5 WHERE detection_id='id'")
    assert start_cursor(db, 0, 2, 8) == 8
    records = [
        SimpleNamespace(offset=i, value=b'{"source_id":"live","detection_id":"id"}')
        for i in (8, 9)
    ]
    account_records(db, 0, records)
    db.close()
    db = sqlite3.connect(path, isolation_level=None)
    try:
        install(db)
        assert start_cursor(db, 0, 3, 10) == 10
        account_records(db, 0, records)
        db.execute("DELETE FROM observations WHERE occurrence_id='live'")
        assert db.execute(
            "SELECT published,sql_visible FROM throughput_source"
        ).fetchone() == (2, 6)
        assert start_cursor(db, 0, 13, 15) == 13
        assert db.execute(
            "SELECT missed_offsets FROM throughput_cursor"
        ).fetchone() == (3,)
    finally:
        db.close()


if __name__ == "__main__":
    with TemporaryDirectory() as folder:
        verify(Path(folder) / "ledger.sqlite")
    print("PASS · SQL rollback, old evidence, replay, restart and Kafka retention gap")
