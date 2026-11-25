"""Resume detector occurrences from the last durably committed frame."""

import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from backend import (
    BUCKET,
    expire,
    provision,
    store,
    visibility_loop,
    search_visibility_loop,
)
from db import DATA, budget, busy, connect, initialize, local, state

STOP = threading.Event()
CHILDREN = set()
LOCK = threading.Lock()


def stop(*_):
    STOP.set()
    with LOCK:
        for child in CHILDREN:
            child.terminate()


def claim(db, run_id):
    db.execute("BEGIN IMMEDIATE")
    try:
        row = db.execute(
            """SELECT * FROM occurrences o WHERE run_id=? AND state IN ('queued','retry')
             AND retry_after<=? AND NOT EXISTS(SELECT 1 FROM occurrences earlier
             WHERE earlier.run_id=o.run_id AND earlier.source_id=o.source_id AND earlier.sequence<o.sequence
             AND earlier.state IN ('queued','processing','retry')) ORDER BY scheduled_at LIMIT 1""",
            (run_id, time.time()),
        ).fetchone()
        if row:
            db.execute(
                "UPDATE occurrences SET state='processing',started_at=coalesce(started_at,?),"
                "attempts=attempts+1 WHERE occurrence_id=?",
                (time.time(), row["occurrence_id"]),
            )
            if row["attempts"]:
                db.execute(
                    "INSERT INTO recoveries(at,occurrence_id,previous_last) VALUES(?,?,?)",
                    (time.time(), row["occurrence_id"], row["last_frame"]),
                )
        db.commit()
        return row
    except BaseException:
        db.rollback()
        raise


def crop(s3, oid, event):
    import uuid

    did = str(uuid.UUID(event["detection_id"]))
    key = "crops/" + did + ".png"
    if event["crop_uri"] != f"s3://{BUCKET}/{key}":
        raise ValueError("Unexpected crop URI")
    path = local("crops/" + oid + "/" + did + ".png")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.exists():
        obj = s3.get_object(Bucket=BUCKET, Key=key)["Body"]
        try:
            data = obj.read()
        finally:
            obj.close()
        if hashlib.sha256(data).hexdigest() != event["crop_sha256"]:
            raise ValueError("Crop digest mismatch")
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
    return str(path.relative_to(DATA))


def commit_frame(db, s3, occurrence, frame, metrics, recovery):
    oid = occurrence["occurrence_id"]
    if frame["source_id"] != oid or frame["asset_sha256"] != occurrence["asset_sha256"]:
        raise ValueError("Detector returned the wrong source or clip")
    index = frame["frame_index"]
    events = frame["detections"]
    images = [crop(s3, oid, x["event"]) for x in events]
    db.execute("BEGIN IMMEDIATE")
    try:
        last = db.execute(
            "SELECT last_frame FROM occurrences WHERE occurrence_id=?", (oid,)
        ).fetchone()[0]
        if index not in (last, last + 1):
            raise ValueError(f"Noncontiguous frame {index} after {last}")
        replay = index == last
        now = time.time()
        pts = frame["pts"]
        num, den = frame["time_base"]
        width, height = frame["frame_size"]
        db.execute(
            "INSERT OR IGNORE INTO frames VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                oid,
                index,
                pts,
                num,
                den,
                pts * num / den,
                width,
                height,
                frame["frame_ingested_at"],
                now,
                len(events),
                frame["manifest_key"],
                json.dumps(metrics),
            ),
        )
        if replay:
            saved = [
                r[0]
                for r in db.execute(
                    "SELECT detection_id FROM observations WHERE occurrence_id=? AND frame_index=?",
                    (oid, index),
                )
            ]
            if set(saved) != {x["event"]["detection_id"] for x in events}:
                raise ValueError("Detection set changed on replay")
        for packet, path in zip(events, images):
            e = packet["event"]
            values = (
                e["detection_id"],
                oid,
                index,
                json.dumps(e),
                path,
                packet["topic"],
                packet["partition"],
                packet["offset"],
                packet["offset"],
            )
            if replay:
                old = db.execute(
                    "SELECT * FROM observations WHERE detection_id=?",
                    (e["detection_id"],),
                ).fetchone()
                if (
                    json.loads(old["event_json"]) != e
                    or old["partition_id"] != packet["partition"]
                    or old["latest_offset"] >= packet["offset"]
                ):
                    raise ValueError(
                        "Replay payload, partition or offset did not match"
                    )
                db.execute(
                    "INSERT INTO replay_events VALUES(?,?,?,?,?,?)",
                    (
                        now,
                        oid,
                        index,
                        e["detection_id"],
                        old["latest_offset"],
                        packet["offset"],
                    ),
                )
                db.execute(
                    "UPDATE observations SET latest_offset=? WHERE detection_id=?",
                    (packet["offset"], e["detection_id"]),
                )
            else:
                db.execute(
                    "INSERT INTO observations(detection_id,occurrence_id,frame_index,event_json,crop_path,topic,"
                    "partition_id,kafka_offset,latest_offset) VALUES(?,?,?,?,?,?,?,?,?)",
                    values,
                )
        if replay and not events:
            db.execute(
                "INSERT INTO replay_events VALUES(?,?,?,NULL,NULL,NULL)",
                (now, oid, index),
            )
        if recovery:
            db.execute(
                "UPDATE recoveries SET first_recovered=? WHERE occurrence_id=? AND first_recovered IS NULL",
                (index, oid),
            )
        db.execute(
            "UPDATE run_totals SET frames=frames+?,faces=faces+?,replayed_frames=replayed_frames+? WHERE run_id=?",
            (
                int(not replay),
                0 if replay else len(events),
                int(replay),
                occurrence["run_id"],
            ),
        )
        db.execute(
            "UPDATE occurrences SET last_frame=?,last_error=NULL WHERE occurrence_id=?",
            (index, oid),
        )
        db.commit()
    except BaseException:
        db.rollback()
        raise


def launch():
    child = subprocess.Popen(
        ["/opt/worker/bin/detection-worker", "serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    with LOCK:
        CHILDREN.add(child)
    return child


def release(child):
    if child:
        child.terminate()
        try:
            child.wait(timeout=8)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
        with LOCK:
            CHILDREN.discard(child)


def slot(slot_id):
    db, s3 = connect(), store()
    label = "worker:" + str(slot_id)
    child = None
    try:
        while not STOP.is_set():
            if not budget():
                state(db, label, "storage_guard", "Reserved disk headroom reached")
                STOP.wait(1)
                continue
            try:
                row = claim(db, os.environ["FEED_RUN_ID"])
            except sqlite3.OperationalError as error:
                if not busy(error):
                    raise
                STOP.wait(0.12)
                continue
            if not row:
                state(db, label, "idle")
                STOP.wait(0.12)
                continue
            oid = row["occurrence_id"]
            try:
                if child is None:
                    child = launch()
                request = {
                    "request_id": oid,
                    "clip": str(local(row["input_path"])),
                    "source_id": oid,
                    "speed": row["speed"],
                    "scheduled_at": row["scheduled_at"],
                    "start_index": max(0, row["last_frame"]),
                }
                child.stdin.write(json.dumps(request) + "\n")
                child.stdin.flush()
                state(db, label, "processing", oid)
                recovering = bool(row["attempts"])
                metrics = {}
                for line in child.stdout:
                    if line.startswith("frame_received="):
                        received = json.loads(line.split("=", 1)[1])
                        if received["source_id"] != oid:
                            raise ValueError(
                                "Received different occurrence from resident detector"
                            )
                        now = time.time()
                        db.execute("BEGIN IMMEDIATE")
                        db.execute(
                            "INSERT OR REPLACE INTO source_received VALUES(?,?,?,?,?,?,?,?)",
                            (
                                row["source_id"],
                                row["run_id"],
                                oid,
                                received["frame_index"],
                                received["pts"],
                                received["ingested_at"],
                                now,
                                received["lag_ms"],
                            ),
                        )
                        db.execute(
                            "INSERT INTO receipts VALUES(?,?,?)",
                            (oid, received["frame_index"], now),
                        )
                        db.commit()
                    elif line.startswith("frame_stats="):
                        metrics = json.loads(line.split("=", 1)[1])
                    elif line.startswith("frame_event="):
                        if not budget():
                            raise RuntimeError("Reserved disk headroom reached")
                        frame = json.loads(line.split("=", 1)[1])
                        commit_frame(db, s3, row, frame, metrics, recovering)
                        state(db, label, "processing", oid)
                        recovering = False
                        metrics = {}
                    elif line.startswith("clip_done="):
                        done = json.loads(line.split("=", 1)[1])
                        last = db.execute(
                            "SELECT last_frame FROM occurrences WHERE occurrence_id=?",
                            (oid,),
                        ).fetchone()[0]
                        if done["request_id"] != oid or last + 1 != row["frame_count"]:
                            raise ValueError(
                                "Detector ended before all source frames were committed"
                            )
                        db.execute("BEGIN IMMEDIATE")
                        db.execute(
                            "UPDATE occurrences SET state='complete',finished_at=? WHERE occurrence_id=?",
                            (time.time(), oid),
                        )
                        db.execute(
                            "UPDATE run_totals SET occurrences=occurrences+1 WHERE run_id=?",
                            (row["run_id"],),
                        )
                        db.commit()
                        break
                    elif line.startswith(
                        (
                            "ort_profile_",
                            "onnxruntime_provider_requested=",
                            "reusable_input=",
                        )
                    ):
                        print(line.rstrip(), flush=True)
                else:
                    raise RuntimeError(f"Detector exited {child.wait()}")
            except Exception as error:
                db.rollback()
                release(child)
                child = None
                message = f"{type(error).__name__}: {error}"[:500]
                db.execute(
                    "UPDATE occurrences SET state='retry',last_error=?,retry_after=? WHERE occurrence_id=?",
                    (message, time.time() + min(30, 2 ** min(row["attempts"], 5)), oid),
                )
                state(db, label, "retry", message)
                STOP.wait(1)
    finally:
        release(child)
        db.close()
        s3.close()


def run():
    import fcntl

    initialize()
    lock = open(DATA / "worker.lock", "a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    db = connect()
    while not STOP.is_set():
        try:
            provision()
            break
        except Exception as exc:
            state(db, "worker", "waiting_backend", str(exc)[:400])
            STOP.wait(3)
    db.execute(
        "UPDATE occurrences SET state='retry',retry_after=0,last_error='Worker restarted' "
        "WHERE run_id=? AND state='processing'",
        (os.environ["FEED_RUN_ID"],),
    )
    for target in (visibility_loop, search_visibility_loop, expire):
        threading.Thread(target=target, daemon=True).start()
    workers = int(os.environ.get("FEED_WORKERS", "1"))
    if not 1 <= workers <= 8:
        raise ValueError("FEED_WORKERS must be 1 through 8")
    supervise(db, workers)


def supervise(db, workers):
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs = [pool.submit(slot, i) for i in range(workers)]
        try:
            while not STOP.is_set():
                for job in jobs:
                    if job.done():
                        job.result()
                        if not STOP.is_set():
                            raise RuntimeError(
                                "Detector slot returned while worker was active"
                            )
                state(db, "worker", "active")
                STOP.wait(0.5)
        finally:
            # Release the resident JVMs and slots before the executor joins them,
            # including when a fatal supervisor or slot error makes Compose restart.
            stop()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    run()
