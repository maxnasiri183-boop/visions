"""Incrementally embed observations, decide against one frozen gallery and retain lineage."""

import argparse
from collections import OrderedDict
from datetime import datetime
import json
import logging
from pathlib import Path
import signal
import sqlite3
import time

import cv2
import numpy as np

from . import core as c


class Decoder:
    def __init__(self):
        self.clips = OrderedDict()

    def frame(self, path, index):
        key = str(c.checked_path(path))
        if key not in self.clips:
            if len(self.clips) == 8:
                self.clips.popitem(last=False)[1][0].release()
            cap = cv2.VideoCapture(key, cv2.CAP_FFMPEG, [cv2.CAP_PROP_N_THREADS, 1])
            self.clips[key] = (cap, -1, None)
        self.clips.move_to_end(key)
        cap, before, image = self.clips[key]
        if before != index:
            if before + 1 != index and not cap.set(cv2.CAP_PROP_POS_FRAMES, index):
                raise ValueError("Cannot seek source frame")
            ok, image = cap.read()
            if not ok:
                self.clips.pop(key)[0].release()
                raise ValueError("Could not decode retained source")
            self.clips[key] = (cap, index, image)
        return image


def attached(db):
    path = c.ROOT / "feed.sqlite"
    if not path.is_file():
        raise FileNotFoundError("Waiting for feed database")
    db.execute("ATTACH DATABASE ? AS feed", (str(path),))


def batch(db, after, limit=32):
    return db.execute(
        """SELECT o.rowid AS sequence,o.*,f.media_pts_s,f.width,f.height,
        u.source_id,u.input_path FROM feed.observations o JOIN feed.occurrences u USING(occurrence_id)
        JOIN feed.frames f USING(occurrence_id,frame_index) LEFT JOIN indexed i USING(detection_id)
        WHERE o.rowid>? AND i.detection_id IS NULL AND u.state!='expired'
        ORDER BY o.rowid LIMIT ?""",
        (after, limit),
    ).fetchall()


def classify(vector, refs, threshold):
    if not refs:
        return "unknown", None, None, "No eligible gallery references"
    scores = [c.dot(vector, ref["embedding"]) for ref in refs]
    best = max(range(len(refs)), key=scores.__getitem__)
    return (
        "matched" if scores[best] >= threshold else "unknown",
        refs[best]["reference_id"],
        scores[best],
        None if scores[best] >= threshold else "Similarity below gallery threshold",
    )


def process(db, rows, decoder):
    active = db.execute(
        "SELECT * FROM versions WHERE model=? AND preprocessing=? AND policy=? ORDER BY version DESC LIMIT 1",
        (c.MODEL, c.PRE, c.POLICY),
    ).fetchone()
    ver = active["version"] if active else None
    cutoff = active["threshold"] if active else c.BASELINE
    refs = db.execute(
        "SELECT * FROM refs JOIN membership USING(reference_id) WHERE version=? ORDER BY reference_id",
        (ver,),
    ).fetchall()
    for row in rows:
        event = json.loads(row["event_json"])
        reason, vector = None, None
        timings = {}
        try:
            img = decoder.frame(row["input_path"], row["frame_index"])
            if img.shape[:2] != (row["height"], row["width"]):
                raise ValueError("Observation and source geometry differ")
            face = np.array(
                [
                    *event["bbox_xywh"],
                    *np.asarray(event["landmarks_xy"]).flatten(),
                    event["confidence"],
                ],
                np.float32,
            )
            vector, reason = c.features(img, face, timings)
            state = "poor_quality" if reason else "ready"
        except (cv2.error, ValueError, OSError) as exc:
            state, reason = "error", str(exc)[:160]
        lookup = time.monotonic()
        decision = (
            (state, None, None, reason)
            if vector is None
            else classify(vector, refs, cutoff)
        )
        lookup_ms = (time.monotonic() - lookup) * 1000 if vector is not None else None
        stamp = datetime.fromisoformat(
            event["frame_ingested_at"].replace("Z", "+00:00")
        ).timestamp()
        with db:
            wrote = db.execute(
                """INSERT OR IGNORE INTO indexed VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    row["detection_id"],
                    row["occurrence_id"],
                    row["frame_index"],
                    row["source_id"],
                    stamp,
                    time.time(),
                    row["media_pts_s"],
                    row["crop_path"],
                    json.dumps(event["bbox_xywh"]),
                    state,
                    reason,
                    vector,
                    timings.get("align_ms"),
                    timings.get("embed_ms"),
                    lookup_ms,
                ),
            ).rowcount
            if wrote:
                db.execute(
                    "INSERT INTO observation_features VALUES(?,?,?,?)",
                    (row["detection_id"], c.MODEL, c.PRE, c.POLICY),
                )
                state, ref, score, reason = decision
                db.execute(
                    "INSERT INTO decisions VALUES(?,?,?,?,?,?,?)",
                    (row["detection_id"], ver, ref, state, score, cutoff, reason),
                )


def main():
    arg = argparse.ArgumentParser(description=__doc__)
    arg.add_argument(
        "--once", action="store_true", help="Drain available observations, then exit"
    )
    once = arg.parse_args().once
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    heartbeat = Path("/tmp/visions-recognition.ready")
    if not once:
        heartbeat.unlink(missing_ok=True)
    last_heartbeat = 0.0
    decoder = Decoder()
    c.engines()  # Fail startup if the model is missing or its hash differs.
    after, last_sweep = 0, 0
    while not stopping:
        try:
            with c.connect() as db:
                attached(db)
                if time.monotonic() - last_sweep >= 10:
                    # Clear retained evidence by occurrence as the feed's retention job expires it.
                    db.execute(
                        "DELETE FROM indexed WHERE occurrence_id IN (SELECT occurrence_id FROM feed.occurrences WHERE state='expired')"
                    )
                    c.expire_probes(db, 100)
                    db.commit()
                    last_sweep = time.monotonic()
                    after = 0
                rows = batch(db, after)
                if rows:
                    process(db, rows, decoder)
                    after = rows[-1]["sequence"]
                elif once:
                    return
                else:
                    time.sleep(0.12)
                if not once and time.monotonic() - last_heartbeat >= 3:
                    heartbeat.touch()
                    last_heartbeat = time.monotonic()
        except (FileNotFoundError, sqlite3.OperationalError) as exc:
            if once:
                raise
            logging.warning("Recognition worker waiting: %s", exc)
            time.sleep(1)


if __name__ == "__main__":
    main()
