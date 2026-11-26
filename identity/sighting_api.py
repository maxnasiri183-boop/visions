"""Read-only identity-first pilot API."""

import math
import os
import sqlite3
import time
from contextlib import contextmanager

from flask import Blueprint, abort, jsonify, request

from . import core

bp = Blueprint("sightings", __name__, url_prefix="/api/operator/identified")


@bp.before_request
def gate():
    if os.getenv("IDENTITY_FIRST_PILOT") != "1":
        abort(404)


@contextmanager
def reader():
    db = sqlite3.connect(
        (core.ROOT / "identity.sqlite").as_uri() + "?mode=ro", uri=True
    )
    try:
        db.row_factory = sqlite3.Row
        db.execute(
            "ATTACH DATABASE ? AS feed",
            ((core.ROOT / "feed.sqlite").as_uri() + "?mode=ro",),
        )
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='sightings'"
        ).fetchone():
            abort(503)
        yield db
    finally:
        db.close()


def number(key, default):
    try:
        n = float(request.args.get(key, default))
    except ValueError:
        abort(400)
    if not math.isfinite(n):
        abort(400)
    return n


def filters():
    now = time.time()
    hours = number("hours", 24)
    if not 0 < hours <= 24 * 365:
        abort(400)
    lo, hi = number("from", now - 3600 * hours), number("to", now)
    if lo >= hi:
        abort(400)
    return (lo, hi, request.args.get("camera", ""), request.args.get("camera", ""))


FILTER = "reviewed_at IS NOT NULL AND last>=? AND first<? AND (?='' OR source=?)"


@bp.get("/people")
def people():
    with reader() as db:
        args = filters()
        offset = request.args.get("offset", 0, type=int)
        if offset < 0:
            abort(400)
        rows = db.execute(
            f"""WITH ranked AS (
            SELECT s.*,r.display_name,count(*) OVER(PARTITION BY person) AS appearances,
            row_number() OVER(PARTITION BY person ORDER BY last DESC,id) AS pos
            FROM sightings s JOIN refs r USING(reference_id) WHERE {FILTER})
            SELECT person,display_name,reference_id,last,source,appearances FROM ranked
            WHERE pos=1 ORDER BY last DESC,person LIMIT 51 OFFSET ?""",
            (*args, offset),
        ).fetchall()
        cameras = [
            r[0]
            for r in db.execute(
                "SELECT DISTINCT source FROM sightings WHERE reviewed_at IS NOT NULL ORDER BY source"
            )
        ]
        return jsonify(
            items=[dict(r) for r in rows[:50]],
            next_offset=offset + 50 if len(rows) > 50 else None,
            cameras=cameras,
        )


@bp.get("/appearances")
def appearances():
    with reader() as db:
        offset = request.args.get("offset", 0, type=int)
        if offset < 0:
            abort(400)
        rows = db.execute(
            f"""SELECT id,source,first,last FROM sightings WHERE {FILTER}
            AND person=? ORDER BY last DESC,id LIMIT 51 OFFSET ?""",
            (*filters(), request.args.get("person"), offset),
        ).fetchall()
        return jsonify(
            items=[dict(r) for r in rows[:50]],
            next_offset=offset + 50 if len(rows) > 50 else None,
        )


def saved(db, key):
    row = db.execute(
        "SELECT * FROM sightings WHERE id=? AND reviewed_at IS NOT NULL", (key,)
    ).fetchone()
    if not row:
        abort(404)
    return row


@bp.get("/appearances/<key>")
def detail(key):
    with reader() as db:
        s = saved(db, key)
        ev = db.execute(
            """SELECT i.detection_id,i.occurrence_id,i.frame_index,i.media_pts_s,i.bbox,
            f.width,f.height,o.scheduled_at+i.media_pts_s/o.speed AS at FROM indexed i
            JOIN feed.frames f USING(occurrence_id,frame_index) JOIN feed.occurrences o USING(occurrence_id)
            WHERE detection_id=? AND o.state NOT IN ('expiring','expired')""",
            (s["representative"],),
        ).fetchone()
        return jsonify(
            id=key,
            person=s["person"],
            source=s["source"],
            score=s["score"],
            version=s["version"],
            reference_id=s["reference_id"],
            evidence=dict(ev) if ev else None,
        )


@bp.get("/appearances/<key>/playback")
def playback(key):
    with reader() as db:
        s = saved(db, key)
        lo, hi = number("from", s["first"] - 2), number("to", s["last"] + 2)
        if lo >= hi or lo < s["first"] - 2 or hi > s["last"] + 2:
            abort(400)
        rows = db.execute(
            """SELECT occurrence_id,scheduled_at,duration_s,speed,session_id,media_path FROM feed.occurrences
            WHERE run_id=? AND source_id=? AND (CASE WHEN session_id GLOB 'rtsp-*' THEN session_id ELSE occurrence_id END)=?
            AND scheduled_at<? AND scheduled_at+duration_s/speed>? AND state NOT IN ('expired','expiring')
            ORDER BY scheduled_at,sequence""",
            (s["run"], s["source"], s["continuity"], hi, lo),
        )
        spans = []
        covered = lo
        missing = False
        for r in rows:
            oid, anchor, duration, speed, session, media_path = r
            path = (core.ROOT / media_path).resolve()
            if not path.is_relative_to(core.ROOT) or not path.is_file():
                continue
            start = max(covered, anchor)
            end = min(hi, anchor + duration / speed)
            if end <= start:
                continue
            missing |= start - covered > 0.001
            boxes = [
                dict(b)
                for b in db.execute(
                    """SELECT i.detection_id,i.frame_index,i.media_pts_s,i.bbox,
                f.width,f.height,f.pts,f.time_base_num,f.time_base_den,'measured' AS kind
                FROM sighting_members m JOIN indexed i USING(detection_id)
                JOIN feed.frames f USING(occurrence_id,frame_index)
                WHERE appearance=? AND occurrence_id=? ORDER BY frame_index""",
                    (key, oid),
                )
            ]
            spans.append(
                dict(
                    occurrence_id=oid,
                    available_utc=[start, end],
                    wall_anchor_utc=anchor,
                    media_anchor_s=0,
                    media_seconds_per_wall_second=speed,
                    clock_source="recorder_estimate"
                    if session.startswith("rtsp-")
                    else "scheduled_replay",
                    media_range_s=[(start - anchor) * speed, (end - anchor) * speed],
                    media_url=f"/api/operator/recordings/{oid}/media",
                    boxes=boxes,
                )
            )
            covered = end
        if not spans:
            abort(410)
        return jsonify(
            appearance_id=key,
            source_id=s["source"],
            requested_utc=[lo, hi],
            availability="partial" if missing or covered < hi - 0.001 else "full",
            spans=spans,
        )
