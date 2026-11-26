"""Transactional state shared by the playlist, detector and operator workspace."""

import json
import os
import sqlite3
import time
from pathlib import Path

DATA = Path(os.environ.get("OPERATOR_DATA", "/data")).resolve()
DB = DATA / "feed.sqlite"


def connect():
    db = sqlite3.connect(DB, timeout=20, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA synchronous=FULL")
    return db


def initialize():
    DATA.mkdir(mode=0o700, exist_ok=True, parents=True)
    with connect() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA journal_size_limit=16777216")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS retention_pressure(id INTEGER PRIMARY KEY,active INTEGER NOT NULL,changed_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS recording_skips(occurrence_id TEXT PRIMARY KEY, frames INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS occurrences(
          occurrence_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, source_id TEXT NOT NULL,
          asset_id TEXT NOT NULL, asset_sha256 TEXT NOT NULL, session_id TEXT NOT NULL,
          sequence INTEGER NOT NULL, cycle INTEGER NOT NULL, input_path TEXT NOT NULL,
          media_path TEXT NOT NULL, scheduled_at REAL NOT NULL, started_at REAL,
          finished_at REAL, state TEXT NOT NULL, frame_count INTEGER NOT NULL,
          speed REAL NOT NULL, duration_s REAL NOT NULL, clock_origin REAL,
          last_frame INTEGER NOT NULL DEFAULT -1, attempts INTEGER NOT NULL DEFAULT 0,
          last_error TEXT, retry_after REAL DEFAULT 0, synthetic INTEGER NOT NULL,
          UNIQUE(run_id,source_id,sequence));
        CREATE INDEX IF NOT EXISTS occ_source ON occurrences(run_id,source_id,sequence DESC);
        CREATE INDEX IF NOT EXISTS occ_state ON occurrences(state,scheduled_at);
        CREATE TABLE IF NOT EXISTS frames(
          occurrence_id TEXT NOT NULL REFERENCES occurrences(occurrence_id),frame_index INTEGER NOT NULL,
          pts INTEGER NOT NULL,time_base_num INTEGER NOT NULL,time_base_den INTEGER NOT NULL,
          media_pts_s REAL NOT NULL,width INTEGER NOT NULL,height INTEGER NOT NULL,ingested_at TEXT,
          indexed_at REAL NOT NULL,face_count INTEGER NOT NULL,manifest_key TEXT NOT NULL,
          stats_json TEXT NOT NULL, PRIMARY KEY(occurrence_id,frame_index));
        CREATE INDEX IF NOT EXISTS frames_time ON frames(indexed_at);
        CREATE TABLE IF NOT EXISTS observations(
          detection_id TEXT PRIMARY KEY,occurrence_id TEXT NOT NULL,frame_index INTEGER NOT NULL,
          event_json TEXT NOT NULL,crop_path TEXT NOT NULL,topic TEXT NOT NULL,partition_id INTEGER NOT NULL,
          kafka_offset INTEGER NOT NULL,latest_offset INTEGER NOT NULL,sql_visible_at REAL,sql_row TEXT,
          FOREIGN KEY(occurrence_id,frame_index) REFERENCES frames(occurrence_id,frame_index));
        CREATE INDEX IF NOT EXISTS obs_frame ON observations(occurrence_id,frame_index);
        CREATE INDEX IF NOT EXISTS obs_visible ON observations(sql_visible_at);
        CREATE INDEX IF NOT EXISTS obs_sql ON observations(sql_visible_at) WHERE sql_visible_at IS NULL;
        CREATE TABLE IF NOT EXISTS source_received(source_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,
          occurrence_id TEXT NOT NULL,frame_index INTEGER NOT NULL,pts INTEGER NOT NULL,
          ingested_at TEXT NOT NULL,observed_at REAL NOT NULL,lag_ms REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS receipts(occurrence_id TEXT NOT NULL,frame_index INTEGER NOT NULL,
          observed_at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS receipt_time ON receipts(observed_at);
        CREATE INDEX IF NOT EXISTS receipt_occ ON receipts(occurrence_id);
        CREATE TABLE IF NOT EXISTS replay_events(
          at REAL NOT NULL, occurrence_id TEXT NOT NULL,frame_index INTEGER NOT NULL,detection_id TEXT,
          old_offset INTEGER,new_offset INTEGER);
        CREATE TABLE IF NOT EXISTS service_state(
          service TEXT PRIMARY KEY,heartbeat_at REAL NOT NULL,state TEXT NOT NULL,detail TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS run_totals(run_id TEXT PRIMARY KEY,frames INTEGER NOT NULL DEFAULT 0,
          faces INTEGER NOT NULL DEFAULT 0,replayed_frames INTEGER NOT NULL DEFAULT 0,
          occurrences INTEGER NOT NULL DEFAULT 0,expired INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY,created_at REAL NOT NULL,
          stop_at REAL,state TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS media_assets(
          checksum TEXT PRIMARY KEY,input_path TEXT NOT NULL,media_path TEXT NOT NULL,
          frame_count INTEGER NOT NULL,duration_s REAL NOT NULL,created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS expiry_counts(occurrence_id TEXT PRIMARY KEY,
          detections INTEGER,missing_sql INTEGER,missing_identity INTEGER,identity_errors INTEGER);
        CREATE TABLE IF NOT EXISTS recoveries(at REAL NOT NULL,occurrence_id TEXT NOT NULL,
          previous_last INTEGER NOT NULL,first_recovered INTEGER,new_frames INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS notices(at REAL NOT NULL,service TEXT NOT NULL,state TEXT NOT NULL,detail TEXT NOT NULL);
        """)
        db.execute("BEGIN IMMEDIATE")
        if "expired_at" not in {
            r[1] for r in db.execute("PRAGMA table_info(occurrences)")
        }:
            db.execute("ALTER TABLE occurrences ADD COLUMN expired_at REAL")
        columns = {r[1] for r in db.execute("PRAGMA table_info(observations)")}
        for column, kind in (("search_visible_at", "REAL"), ("search_state", "TEXT")):
            if column not in columns:
                db.execute(f"ALTER TABLE observations ADD COLUMN {column} {kind}")
        db.execute(
            "CREATE INDEX IF NOT EXISTS obs_unindexed ON observations(search_state) WHERE search_state IS NULL"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS obs_searchable ON observations(search_visible_at)"
        )


def busy(error):
    return getattr(error, "sqlite_errorcode", 0) & 255 in (
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_LOCKED,
    )


def state(db, service, status, detail=""):
    # A telemetry write may time out while frame transactions compete for SQLite's
    # writer lock. Log the attempted transition and retry on the next heartbeat.
    try:
        now = time.time()
        old = db.execute(
            "SELECT state,detail,heartbeat_at FROM service_state WHERE service=?",
            (service,),
        ).fetchone()
        if (
            old is not None
            and tuple(old)[:2] == (status, detail)
            and now - old["heartbeat_at"] < 2
        ):
            return
        changed = old is None or tuple(old)[:2] != (status, detail)
        if changed:
            db.execute(
                "INSERT INTO notices VALUES(?,?,?,?)", (now, service, status, detail)
            )
        db.execute(
            "INSERT OR REPLACE INTO service_state VALUES(?,?,?,?)",
            (service, now, status, detail),
        )
        if changed:
            print(
                json.dumps(
                    {"service": service, "state": status, "detail": detail, "at": now}
                ),
                flush=True,
            )
    except sqlite3.OperationalError as error:
        if not busy(error):
            raise
        print(
            json.dumps(
                {
                    "service": service,
                    "state_write": "sqlite_busy",
                    "requested": status,
                    "detail": detail,
                    "at": time.time(),
                }
            ),
            flush=True,
        )


def local(path):
    path = (DATA / path).resolve()
    if not path.is_relative_to(DATA) or path == DATA:
        raise ValueError("Asset path must be within the operator data directory")
    return path


def budget():
    import shutil

    disk = shutil.disk_usage(DATA)
    reserve = int(os.environ.get("FEED_MIN_FREE_BYTES", str(24 * 1024**3)))
    if os.getenv("FEED_RETENTION_MODE") == "disk":
        # Keep recording during cleanup unless the volume approaches exhaustion.
        reserve = min(reserve, 1024**3)
    return disk.free >= reserve


def bounded_id(value):
    import re

    if not isinstance(value, str) or not re.fullmatch(
        r"[\w.-]{1,128}", value, flags=re.ASCII
    ):
        raise ValueError("Expected a bounded source, playlist or run identifier")
    return value
