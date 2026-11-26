"""Check a fresh solo recording from the promoted private research corpus, read-only."""

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from urllib.parse import urlencode
from urllib.request import urlopen
from zipfile import ZipFile

import cv2
import numpy as np
import pymysql

from .vidtimit_candidate import original_frames


def read(path):
    return json.loads(path.read_text())


def get(base, path):
    with urlopen(base + path, timeout=15) as response:
        return response.read()


def known_solo(target, version, manifest, aliases, enrolled):
    with closing(
        sqlite3.connect(
            (target / "identity.sqlite").as_uri() + "?mode=ro", uri=True, timeout=15
        )
    ) as db:
        db.row_factory = sqlite3.Row
        db.execute(
            "ATTACH DATABASE ? AS feed",
            ((target / "feed.sqlite").as_uri() + "?mode=ro",),
        )
        rows = db.execute(
            """
          SELECT o.detection_id,o.event_json,o.sql_row,o.topic,o.partition_id,o.kafka_offset,o.sql_visible_at,
            u.occurrence_id,u.asset_id,u.asset_sha256,u.session_id,u.started_at,u.source_id,
            f.frame_index,f.media_pts_s,r.person_id,r.display_name,d.version,d.state,v.created_at,v.threshold,d.score
            FROM feed.occurrences u JOIN feed.observations o USING(occurrence_id)
            JOIN feed.frames f USING(occurrence_id,frame_index) JOIN indexed i USING(detection_id)
            JOIN decisions d USING(detection_id) JOIN refs r USING(reference_id) JOIN versions v ON v.version=d.version
            WHERE d.version=? AND u.source_id='studio' AND u.state='complete' AND u.asset_id LIKE 'vt-%'
              AND u.started_at>=v.created_at AND o.sql_visible_at IS NOT NULL AND i.state='ready' AND d.state='matched'
            ORDER BY u.started_at,f.frame_index
        """,
            (version,),
        )
        for row in rows:
            source = manifest.get(row["asset_sha256"])
            if source is None or source["person_id"] not in enrolled:
                continue
            alias = aliases[source["person_id"]]
            if (
                row["person_id"] == alias["person_id"]
                and row["session_id"] == alias["video_session"]
            ):
                return dict(row), source
    return None


def verify(args, row, source):
    event = json.loads(row["event_json"])
    sql = json.loads(row["sql_row"])
    if (
        event["source_id"] != row["occurrence_id"]
        or event["detection_id"] != row["detection_id"]
        or event["frame_index"] != row["frame_index"]
    ):
        raise ValueError("Event identity or original frame differs")
    for key, expected in event.items():
        observed = (
            json.loads(sql[key])
            if isinstance(expected, list) and isinstance(sql[key], str)
            else sql[key]
        )
        if observed != expected:
            raise ValueError("Stored SQL observation differs from event field " + key)
    with (
        pymysql.connect(
            host=args.sql_host,
            port=args.sql_port,
            user=args.sql_user,
            read_timeout=10,
            database="visions_operator",
        ) as remote,
        remote.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT count(*) FROM face_detections WHERE detection_id=%s",
            (row["detection_id"],),
        )
        count = cursor.fetchone()[0]
    if count != 1:
        raise ValueError("Expected one logical row in StarRocks")

    identifier = row["detection_id"]
    crop = get(args.base, "/api/operator/crops/" + identifier)
    full = get(args.base, "/api/operator/original/" + identifier + ".png")
    decision = json.loads(get(args.base, "/api/identity/observations/" + identifier))
    if hashlib.sha256(crop).hexdigest() != event["crop_sha256"]:
        raise ValueError("Served crop differs from stored event")
    if (decision["version"], decision["state"], decision["person_id"]) != (
        args.version,
        "matched",
        row["person_id"],
    ):
        raise ValueError("Served recognition decision differs from the stored match")

    def pixels(raw):
        return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)

    frame = pixels(full)
    with ZipFile(args.candidate / "archives" / source["archive"]) as archive:
        names = original_frames(archive, source["person_id"], source["take"])
        expected = pixels(archive.read(names[row["frame_index"]]))
    x, y, w, h = event["bbox_xywh"]
    face = pixels(crop)
    if (
        face.shape[:2] != (h, w)
        or frame.shape != expected.shape
        or abs(row["media_pts_s"] - row["frame_index"] / source["fps"]) >= 0.001
    ):
        raise ValueError("Original frame, crop geometry or media timestamp differs")
    # libjpeg and FFmpeg can differ in chroma upsampling. Compare the decoded pixels as well as the exact stored digest.
    frame_mae = float(np.abs(frame.astype(np.int16) - expected.astype(np.int16)).mean())
    crop_mae = float(
        np.abs(
            frame[y : y + h, x : x + w].astype(np.int16) - face.astype(np.int16)
        ).mean()
    )
    if max(frame_mae, crop_mae) >= 5:
        raise ValueError("Original or crop pixel mean absolute error exceeds the bound")
    query = urlencode(
        {
            "view": "recordings",
            "occ": row["occurrence_id"],
            "frame": row["frame_index"],
            "face": identifier,
        }
    )
    return {
        **{
            key: row[key]
            for key in (
                "version",
                "source_id",
                "asset_id",
                "session_id",
                "occurrence_id",
                "frame_index",
                "media_pts_s",
                "detection_id",
                "person_id",
                "display_name",
                "state",
                "score",
                "threshold",
                "topic",
                "partition_id",
                "kafka_offset",
                "sql_visible_at",
            )
        },
        "fresh_after_publication": row["started_at"] >= row["created_at"],
        "correct_against_independent_capture": True,
        "sql_row_count": count,
        "crop_sha256_verified": True,
        "frame_mae_vs_original": frame_mae,
        "crop_mae_vs_frame": crop_mae,
        "operator_url": args.base + "/?" + query,
    }


def run(args):
    args.target = args.target.resolve()
    args.candidate = args.candidate.resolve()
    manifest = {r["sha256"]: r for r in read(args.candidate / "stream.json")}
    aliases = read(args.candidate / "private-alias-crosswalk.json")
    enrolled = {
        who
        for who, role in read(args.candidate / "split-plan.json").items()
        if role["enrolled"]
    }
    deadline = time.monotonic() + args.wait
    while True:
        found = known_solo(args.target, args.version, manifest, aliases, enrolled)
        if found:
            return verify(args, *found)
        if time.monotonic() >= deadline:
            raise TimeoutError(
                "No completed fresh solo observation matched a verified enrolled identity"
            )
        time.sleep(2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--version", type=int, required=True)
    parser.add_argument("--base", default="http://127.0.0.1:8092")
    parser.add_argument("--sql-host", default="127.0.0.1")
    parser.add_argument("--sql-port", type=int, default=19030)
    parser.add_argument("--sql-user", default="root")
    parser.add_argument("--wait", type=int, default=480)
    print(json.dumps(run(parser.parse_args()), indent=2))
