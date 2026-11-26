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
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

from .db import DATA, bounded_id, budget, connect, initialize, local, state


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


class Catalog:
    """Prepare one private asset at a time without delaying source playout."""

    def __init__(self):
        self.pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="catalog-media"
        )
        self.pending = {}
        self.ready = {}
        self.failures = {}
        self.previous = {}

    @staticmethod
    def prepare(entry):
        # SQLite connections stay on their own thread. Media publication is atomic in asset().
        with closing(connect()) as db:
            return asset(db, entry)

    def collect(self):
        for key, future in list(self.pending.items()):
            if not future.done():
                continue
            del self.pending[key]
            try:
                self.ready[key] = future.result()
                self.failures.pop(key, None)
            except (
                OSError,
                ValueError,
                KeyError,
                IndexError,
                sqlite3.Error,
                subprocess.SubprocessError,
            ) as error:
                self.failures[key] = (time.monotonic() + 5, str(error))

    def schedule(self, db, waiting):
        capacity = 2 - len(self.pending)
        if not waiting or capacity <= 0:
            return
        retained = {r[0] for r in db.execute("SELECT checksum FROM media_assets")}
        # Verify retained assets first on process restart, and start each camera's first
        # usable clip before a single camera can occupy the entire build queue.
        ordered = sorted(
            waiting,
            key=lambda k: (waiting[k][1].get("sha256") not in retained, waiting[k][0]),
        )
        for key in ordered:
            if (
                key in self.pending
                or self.failures.get(key, (0,))[0] > time.monotonic()
            ):
                continue
            self.pending[key] = self.pool.submit(self.prepare, waiting[key][1])
            capacity -= 1
            if not capacity:
                break

    def read(self, db):
        self.collect()
        entries = json.loads((DATA / "catalog.json").read_text())
        faults = DATA / "outages.json"
        outages = json.loads(faults.read_text()) if faults.exists() else {}
        ready = defaultdict(list)
        counts = defaultdict(int)
        waiting = {}
        unavailable = {}
        keys = set()
        for entry in entries:
            camera = bounded_id(entry["source_id"])
            counts[camera] += 1
            if float(outages.get(camera, 0)) > time.time():
                unavailable[camera] = "Controlled source interruption"
                continue
            bounded_id(entry["asset_id"])
            bounded_id(entry["session_id"])
            try:
                path = local(entry["path"])
                stat = path.stat()
                key = (path, stat.st_mtime_ns, stat.st_size, entry.get("sha256"))
                keys.add(key)
                media = self.ready.get(key)
                if media and all(
                    local(media[name]).is_file()
                    for name in ("input_path", "media_path")
                ):
                    ready[camera].append((entry, media))
                else:
                    self.ready.pop(key, None)
                    waiting[key] = (counts[camera], entry)
                    if key in self.failures:
                        unavailable[camera] = self.failures[key][1]
            except (OSError, ValueError) as error:
                unavailable[camera] = str(error)
        # A changing catalog cannot grow the preparation cache indefinitely.
        for cache in (self.ready, self.failures):
            for key in cache.keys() - keys:
                del cache[key]
        self.schedule(db, waiting)
        state(
            db,
            "catalog-preparation",
            "preparing" if waiting else "ready",
            f"{len(waiting)} pending assets" if waiting else "",
        )
        groups = {}
        for camera, total in counts.items():
            if camera in unavailable:
                state(db, "source:" + camera, "source_outage", unavailable[camera])
                continue
            choices = ready[camera]
            remaining = total - len(choices)
            if not remaining:
                self.previous[camera] = {
                    (e["asset_id"], m["checksum"]) for e, m in choices
                }
            elif camera in self.previous:
                # Keep the prior rotation during a batch addition. Immediately exclude
                # removed/replaced media, even when the next snapshot is still building.
                kept = [
                    (e, m)
                    for e, m in choices
                    if (e["asset_id"], m["checksum"]) in self.previous[camera]
                ]
                choices = kept or choices
            if choices:
                groups[camera] = choices
            detail = f"Preparing {remaining} playlist assets" if remaining else ""
            state(db, "source:" + camera, "online" if choices else "preparing", detail)
        for camera in self.previous.keys() - counts.keys():
            del self.previous[camera]
        for row in db.execute(
            "SELECT service FROM service_state WHERE service LIKE 'source:%'"
        ):
            if row[0].split(":", 1)[1] not in counts:
                state(db, row[0], "source_outage", "Source removed from active catalog")
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
    db.execute("""CREATE TABLE IF NOT EXISTS replay_controls(
        source_id TEXT PRIMARY KEY, speed REAL NOT NULL, changed_at REAL NOT NULL)""")
    catalog = Catalog()
    while True:
        try:
            now = time.time()
            run_row = db.execute(
                "SELECT * FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            timed_stop = (
                os.getenv("FEED_KEEP_LIVE", "0") != "1" and now >= run_row["stop_at"]
            )
            if run_row["state"] != "running" or timed_stop:
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
            groups = catalog.read(db)
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
                setting = db.execute(
                    "SELECT speed,changed_at FROM replay_controls WHERE source_id=?",
                    (camera,),
                ).fetchone()
                if setting and last and last["scheduled_at"] < setting["changed_at"]:
                    # Finish queued work at the original rate. Start the new rate on its own wall clock.
                    if pending:
                        continue
                    due = max(now, due)
                if due > now:
                    continue
                entry, clip = choices[seq % len(choices)]
                selected_speed = setting[0] if setting else speed
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
                        selected_speed,
                        clip["duration_s"],
                        int(entry.get("synthetic", False)),
                    ),
                )
                state(db, "queue:" + camera, "active")
            status = (
                "active"
                if groups
                else ("preparing" if catalog.pending else "source_outage")
            )
            state(db, "feeder", status, run_id)
        except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
            state(db, "feeder", "source_outage", str(exc))
        time.sleep(0.25)


if __name__ == "__main__":
    run()
