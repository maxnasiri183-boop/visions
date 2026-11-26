"""Record independent RTSP cameras and enqueue finalized segments for the GPU worker."""

import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import threading
import time
import uuid

from .db import DATA, bounded_id, budget, connect, initialize, state

STOP = threading.Event()
RUN = os.environ.get("FEED_RUN_ID", "rtsp")
SECONDS = 2


def register(db, camera, session, path):
    relative = str(path.relative_to(DATA))
    oid = "occ-" + uuid.uuid5(uuid.NAMESPACE_URL, relative).hex
    if db.execute("SELECT 1 FROM occurrences WHERE occurrence_id=?", (oid,)).fetchone():
        return
    probe = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_packets",
            "-show_entries",
            "stream=nb_read_packets:format=duration",
            "-of",
            "json",
            str(path),
        ]
    )
    info = json.loads(probe)
    frames = int(info["streams"][0]["nb_read_packets"])
    duration = float(info["format"]["duration"])
    if frames < 1 or duration <= 0:
        raise ValueError("Empty recording")
    start = path.stat().st_mtime - duration
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    with db:
        db.execute("BEGIN IMMEDIATE")
        # A slow detector may drop queued work. Its recording remains browsable.
        db.execute(
            """INSERT OR IGNORE INTO recording_skips SELECT occurrence_id,frame_count-last_frame-1
            FROM occurrences WHERE run_id=? AND source_id=? AND state IN ('queued','retry')""",
            (RUN, camera),
        )
        db.execute(
            """UPDATE occurrences SET state='recorded',finished_at=? WHERE
            run_id=? AND source_id=? AND state IN ('queued','retry')""",
            (time.time(), RUN, camera),
        )
        seq = db.execute(
            "SELECT coalesce(max(sequence),-1)+1 FROM occurrences WHERE run_id=? AND source_id=?",
            (RUN, camera),
        ).fetchone()[0]
        db.execute(
            """INSERT INTO occurrences(occurrence_id,run_id,source_id,asset_id,asset_sha256,
            session_id,sequence,cycle,input_path,media_path,scheduled_at,state,frame_count,speed,
            duration_s,synthetic) VALUES(?,?,?,?,?,?,?,?,?,?,?,'queued',?,1,?,0)""",
            (
                oid,
                RUN,
                camera,
                camera,
                digest,
                session,
                seq,
                0,
                relative,
                relative,
                start,
                frames,
                duration,
            ),
        )


def recover(db, camera, folder):
    for path in sorted(folder.glob("[0-9]" * 9 + ".mp4")):
        try:
            register(db, camera, folder.name, path)
        except (subprocess.CalledProcessError, KeyError, ValueError) as error:
            # Keep a partial recording for inspection and report the gap.
            state(db, "capture:" + camera, "partial_recording", f"{path.name}: {error}")
    (folder / ".open").unlink(missing_ok=True)
    if not any(folder.iterdir()):
        folder.rmdir()


def capture(db, camera, url, root):
    folder = root / ("rtsp-" + uuid.uuid4().hex)
    folder.mkdir()
    (folder / ".open").touch()
    child = subprocess.Popen(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-rtsp_transport",
            "tcp",
            "-timeout",
            "7000000",
            "-i",
            url,
            "-map",
            "0:v:0",
            "-an",
            "-c:v",
            "copy",
            "-f",
            "segment",
            "-segment_time",
            str(SECONDS),
            "-reset_timestamps",
            "1",
            "-segment_format",
            "mp4",
            "-segment_format_options",
            "movflags=+faststart",
            str(folder / "%09d.mp4"),
        ]
    )
    index = 0
    last = time.monotonic()
    try:
        while not STOP.is_set() and child.poll() is None:
            if not budget():
                state(
                    db, "capture:" + camera, "storage_guard", "Free space below reserve"
                )
                break
            if (folder / f"{index + 1:09d}.mp4").exists():
                register(db, camera, folder.name, folder / f"{index:09d}.mp4")
                index += 1
                last = time.monotonic()
            else:
                status = "online" if index else "connecting"
                state(db, "capture:" + camera, status)
                state(db, "source:" + camera, status)
                if time.monotonic() - last > 35:
                    break
                STOP.wait(0.25)
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(8)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        # Only the tail can be unpublished during ordinary shutdown.
        for path in sorted(folder.glob("*.mp4")):
            if int(path.stem) >= index:
                try:
                    register(db, camera, folder.name, path)
                except (subprocess.CalledProcessError, ValueError, KeyError):
                    state(db, "capture:" + camera, "partial_recording", path.name)
        (folder / ".open").unlink(missing_ok=True)
        if not any(folder.iterdir()):
            folder.rmdir()


def camera_loop(camera, url):
    root = DATA / "recordings" / camera
    root.mkdir(parents=True, exist_ok=True)
    db = connect()
    while not STOP.is_set():
        try:
            for marker in root.glob("*/.open"):
                recover(db, camera, marker.parent)
            if budget():
                capture(db, camera, url, root)
                if budget():
                    state(db, "capture:" + camera, "disconnected", "RTSP session ended")
            else:
                state(
                    db, "capture:" + camera, "storage_guard", "Free space below reserve"
                )
        except (
            OSError,
            sqlite3.Error,
            subprocess.SubprocessError,
            ValueError,
            KeyError,
        ) as error:
            db.rollback()
            state(db, "capture:" + camera, "disconnected", type(error).__name__)
        STOP.wait(2)


def main():
    initialize()
    cameras = json.loads(os.environ["RTSP_CAMERAS"])
    if (
        not isinstance(cameras, dict)
        or not cameras
        or any(
            bounded_id(camera) in (".", "..")
            or not isinstance(url, str)
            or not url.startswith("rtsp://")
            for camera, url in cameras.items()
        )
    ):
        raise ValueError("Configure named RTSP cameras")
    db = connect()
    now = time.time()
    db.execute(
        "INSERT INTO runs VALUES(?,?,NULL,'running') ON CONFLICT(run_id) DO UPDATE SET stop_at=NULL,state='running'",
        (RUN, now),
    )
    db.execute("INSERT OR IGNORE INTO run_totals(run_id) VALUES(?)", (RUN,))
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    threads = [
        threading.Thread(target=camera_loop, args=(bounded_id(c), u))
        for c, u in cameras.items()
    ]
    for t in threads:
        t.start()
    while not STOP.wait(2):
        if not all(t.is_alive() for t in threads):
            STOP.set()
            raise RuntimeError("Recorder thread exited")
        state(db, "feeder", "active", RUN)
    for t in threads:
        t.join()


if __name__ == "__main__":
    main()
