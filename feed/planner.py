"""Pace a rotating private catalog, preserving scheduled source time across restarts."""

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
import uuid
from collections import defaultdict

from db import DATA, bounded_id, budget, connect, initialize, local, state


def asset(db, entry):
    source = local(entry["path"])
    if not source.is_file():
        raise ValueError("Catalog asset is not a regular file")
    with source.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    if entry.get("sha256", checksum) != checksum:
        raise ValueError("Catalog checksum mismatch")
    existing = db.execute(
        "SELECT * FROM media_assets WHERE checksum=?", (checksum,)
    ).fetchone()
    if (
        existing
        and local(existing["input_path"]).is_file()
        and local(existing["media_path"]).is_file()
    ):
        return dict(existing)
    root = DATA / "media" / checksum
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    original = root / ("original" + source.suffix.lower())
    mp4 = root / "seek.mp4"
    with tempfile.TemporaryDirectory(dir=root) as tmp:
        from pathlib import Path

        copy = Path(tmp) / original.name
        shutil.copyfile(source, copy)
        with copy.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != checksum:
                raise ValueError("Input changed during ingestion")
        probe = json.loads(
            subprocess.check_output(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-count_frames",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=nb_read_frames,width,height:format=duration",
                    "-of",
                    "json",
                    str(copy),
                ]
            )
        )
        frames = int(probe["streams"][0]["nb_read_frames"])
        duration = float(probe["format"]["duration"])
        if frames < 1 or duration <= 0:
            raise ValueError("Video has no usable frames or timeline")
        preview = Path(tmp) / "seek.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-noautorotate",
                "-i",
                str(copy),
                "-map",
                "0:v:0",
                "-an",
                "-fps_mode",
                "passthrough",
                "-c:v",
                "libx264",
                "-threads",
                "2",
                "-preset",
                "veryfast",
                "-crf",
                "17",
                "-pix_fmt",
                "yuv420p",
                "-g",
                "30",
                "-movflags",
                "+faststart",
                str(preview),
            ],
            check=True,
        )
        copy.replace(original)
        preview.replace(mp4)
    db.execute(
        "INSERT OR REPLACE INTO media_assets VALUES(?,?,?,?,?,?)",
        (
            checksum,
            str(original.relative_to(DATA)),
            str(mp4.relative_to(DATA)),
            frames,
            duration,
            time.time(),
        ),
    )
    return dict(
        db.execute(
            "SELECT * FROM media_assets WHERE checksum=?", (checksum,)
        ).fetchone()
    )


def read_catalog(db, cached):
    items = json.loads((DATA / "catalog.json").read_text())
    groups = defaultdict(list)
    faults = DATA / "outages.json"
    outages = json.loads(faults.read_text()) if faults.exists() else {}
    seen = set()
    for entry in items:
        camera = bounded_id(entry["source_id"])
        seen.add(camera)
        if float(outages.get(camera, 0)) > time.time():
            state(
                db,
                "source:" + camera,
                "source_outage",
                "Controlled source interruption",
            )
            continue
        bounded_id(entry["asset_id"])
        bounded_id(entry["session_id"])
        try:
            path = local(entry["path"])
            stat = path.stat()
            key = (path, stat.st_mtime_ns, stat.st_size, entry.get("sha256"))
            if key not in cached:
                cached[key] = asset(db, entry)
            groups[camera].append((entry, cached[key]))
            state(db, "source:" + camera, "online")
        except (ValueError, OSError, subprocess.CalledProcessError) as error:
            state(db, "source:" + camera, "source_outage", str(error))
    for r in db.execute(
        "SELECT service FROM service_state WHERE service LIKE 'source:%'"
    ):
        if r[0].split(":", 1)[1] not in seen:
            state(db, r[0], "source_outage", "Source removed from active catalog")
    return groups


def run():
    initialize()
    db = connect()
    run_id = bounded_id(os.environ["FEED_RUN_ID"])
    speed = float(os.environ.get("FEED_SPEED", "1"))
    horizon = float(os.environ.get("FEED_RUN_HOURS", "24")) * 3600
    limit = int(os.environ.get("FEED_QUEUE_PER_SOURCE", "2"))
    stream_limit = int(os.environ.get("FEED_SOURCE_LIMIT", "8"))
    if speed <= 0 or limit < 1 or stream_limit < 1 or horizon <= 0:
        raise ValueError("Rate, duration and queue limits must be positive")
    now = time.time()
    db.execute(
        "INSERT OR IGNORE INTO runs VALUES(?,?,?,'running')",
        (run_id, now, now + horizon),
    )
    db.execute("INSERT OR IGNORE INTO run_totals(run_id) VALUES(?)", (run_id,))
    cache = {}
    while True:
        try:
            now = time.time()
            run_row = db.execute(
                "SELECT * FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if run_row["state"] != "running" or now >= run_row["stop_at"]:
                db.execute(
                    "UPDATE runs SET state='finished' WHERE run_id=? AND state='running'",
                    (run_id,),
                )
                state(db, "feeder", "finished", run_id)
                time.sleep(2)
                continue
            if not budget():
                state(db, "feeder", "storage_guard", "Reserved disk headroom reached")
                time.sleep(2)
                continue
            groups = read_catalog(db, cache)
            for camera, choices in list(groups.items())[:stream_limit]:
                pending = db.execute(
                    "SELECT count(*) FROM occurrences WHERE run_id=? AND source_id=? "
                    "AND state IN ('queued','processing','retry')",
                    (run_id, camera),
                ).fetchone()[0]
                if pending >= limit:
                    state(db, "queue:" + camera, "backpressure")
                    continue
                last = db.execute(
                    "SELECT * FROM occurrences WHERE run_id=? AND source_id=? "
                    "ORDER BY sequence DESC LIMIT 1",
                    (run_id, camera),
                ).fetchone()
                seq = (last["sequence"] + 1) if last else 0
                due = (
                    (last["scheduled_at"] + last["duration_s"] / last["speed"])
                    if last
                    else now
                )
                if due > now:
                    continue
                entry, clip = choices[seq % len(choices)]
                oid = "occ-" + uuid.uuid4().hex
                db.execute(
                    """INSERT INTO occurrences(occurrence_id,run_id,source_id,asset_id,asset_sha256,
                    session_id,sequence,cycle,input_path,media_path,scheduled_at,state,frame_count,speed,
                    duration_s,synthetic) VALUES(?,?,?,?,?,?,?,?,?,?,?,'queued',?,?,?,?)""",
                    (
                        oid,
                        run_id,
                        camera,
                        entry["asset_id"],
                        clip["checksum"],
                        entry["session_id"],
                        seq,
                        seq // len(choices),
                        clip["input_path"],
                        clip["media_path"],
                        due,
                        clip["frame_count"],
                        speed,
                        clip["duration_s"],
                        int(entry.get("synthetic", False)),
                    ),
                )
                state(db, "queue:" + camera, "active")
            state(db, "feeder", "active" if groups else "source_outage", run_id)
        except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
            state(db, "feeder", "source_outage", str(exc))
        time.sleep(0.25)


if __name__ == "__main__":
    run()
