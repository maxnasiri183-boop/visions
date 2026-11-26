"""Build camera-local shadow sightings from frozen gallery decisions."""

import argparse
import json
import time
import uuid
from collections import Counter
from itertools import groupby

from . import core

POLICY = "pilot-iou30-gap1-margin05-v1"
SCHEMA = """
CREATE TABLE IF NOT EXISTS sighting_runs(occurrence_id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS sightings(
 id TEXT PRIMARY KEY, person TEXT NOT NULL, reference_id TEXT NOT NULL, version INTEGER NOT NULL,
 source TEXT NOT NULL, run TEXT NOT NULL, continuity TEXT NOT NULL, first REAL NOT NULL,
 last REAL NOT NULL, representative TEXT NOT NULL, score REAL NOT NULL, policy TEXT NOT NULL,
 tail_box TEXT NOT NULL, tail_sequence INTEGER NOT NULL, closed INTEGER NOT NULL DEFAULT 0,
 reviewed_by TEXT, reviewed_at REAL);
CREATE INDEX IF NOT EXISTS sightings_camera ON sightings(run,source,continuity,last);
CREATE INDEX IF NOT EXISTS sightings_people ON sightings(person,last);
CREATE TABLE IF NOT EXISTS sighting_members(
 detection_id TEXT PRIMARY KEY REFERENCES indexed(detection_id) ON DELETE CASCADE,
 appearance TEXT REFERENCES sightings(id), reason TEXT NOT NULL, runner TEXT, runner_score REAL);
CREATE INDEX IF NOT EXISTS sighting_members_by_id ON sighting_members(appearance);
"""


def overlap(a, b):
    w = max(0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    h = max(0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    return w * h / max(1, a[2] * a[3] + b[2] * b[3] - w * h)


def assess(row, gallery):
    if row["state"] != "matched":
        return row["reason"] or row["state"], None, None
    if row["embedding"] is None or not any(
        ref["reference_id"] == row["reference_id"]
        and ref["person_id"] == row["person_id"]
        for ref in gallery
    ):
        return "missing frozen reference evidence", None, None
    scores = {}
    for ref in gallery:
        person = ref["person_id"]
        if person != row["person_id"]:
            scores[person] = max(
                scores.get(person, -1), core.dot(row["embedding"], ref["embedding"])
            )
    if not scores:
        return "eligible", None, None
    runner = max(scores, key=scores.get)
    score = scores[runner]
    reason = "ambiguous candidates" if row["score"] - score < 0.05 else "eligible"
    return reason, runner, score


def materialize(db, occ):
    camera, run = occ["source_id"], occ["run_id"]
    continuity = (
        occ["session_id"]
        if occ["session_id"].startswith("rtsp-")
        else occ["occurrence_id"]
    )
    rows = db.execute(
        """SELECT i.*,d.state AS verdict,d.score,d.version,d.reference_id,d.reason AS why,r.person_id
        FROM indexed i JOIN decisions d USING(detection_id) LEFT JOIN refs r USING(reference_id)
        WHERE occurrence_id=? ORDER BY frame_index,detection_id""",
        (occ["occurrence_id"],),
    ).fetchall()
    tracks = {}
    for r in db.execute(
        """SELECT s.id,s.person,s.version,s.last,s.tail_box FROM sightings s
        WHERE s.run=? AND s.source=? AND s.continuity=? AND s.reviewed_at IS NULL AND closed=0 AND tail_sequence=?
        AND s.last BETWEEN ? AND ?""",
        (
            run,
            camera,
            continuity,
            occ["sequence"] - 1,
            occ["scheduled_at"] - 1,
            occ["scheduled_at"],
        ),
    ):
        tracks[r["id"]] = (
            r["person"],
            r["version"],
            r["last"],
            json.loads(r["tail_box"]),
        )
    galleries = {}
    for _, group in groupby(rows, key=lambda r: r["frame_index"]):
        frame = list(group)
        people = Counter(r["person_id"] for r in frame if r["verdict"] == "matched")
        boxes = [json.loads(r["bbox"]) for r in frame]
        links = []
        for r, box in zip(frame, boxes):
            at = occ["scheduled_at"] + r["media_pts_s"] / occ["speed"]
            links.append(
                [
                    key
                    for key, (_, _, last, prior) in tracks.items()
                    if 0 <= at - last <= 1 and overlap(box, prior) >= 0.3
                ]
            )
        uses = Counter(k for group_links in links for k in group_links)
        old = tracks.copy()
        for r, box, keys in zip(frame, boxes, links):
            version = r["version"]
            if version not in galleries:
                galleries[version] = db.execute(
                    "SELECT reference_id,person_id,embedding FROM membership JOIN refs USING(reference_id) WHERE version=?",
                    (version,),
                ).fetchall()
            reason, runner, score = assess(
                dict(r, state=r["verdict"], reason=r["why"]), galleries[version]
            )
            person = r["person_id"]
            if len(keys) > 1 or any(uses[k] > 1 for k in keys) or people[person] > 1:
                reason = "ambiguous face association"
            elif keys and old[keys[0]][0] != person and reason == "eligible":
                reason = "conflicting identity"
            key = None
            at = occ["scheduled_at"] + r["media_pts_s"] / occ["speed"]
            if reason == "eligible":
                key = (
                    keys[0]
                    if keys and old[keys[0]][1] == version
                    else str(uuid.uuid5(uuid.NAMESPACE_URL, POLICY + r["detection_id"]))
                )
                db.execute(
                    """INSERT INTO sightings VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,NULL,NULL)
                    ON CONFLICT(id) DO UPDATE SET last=excluded.last,tail_box=excluded.tail_box,tail_sequence=excluded.tail_sequence,
                    representative=CASE WHEN excluded.score>sightings.score THEN excluded.representative ELSE sightings.representative END,
                    reference_id=CASE WHEN excluded.score>sightings.score THEN excluded.reference_id ELSE sightings.reference_id END,
                    score=max(sightings.score,excluded.score)""",
                    (
                        key,
                        person,
                        r["reference_id"],
                        version,
                        camera,
                        run,
                        continuity,
                        at,
                        at,
                        r["detection_id"],
                        r["score"],
                        POLICY,
                        r["bbox"],
                        occ["sequence"],
                    ),
                )
                tracks[key] = (person, version, at, box)
            for previous in keys:
                if previous != key:
                    tracks.pop(previous, None)
                    db.execute("UPDATE sightings SET closed=1 WHERE id=?", (previous,))
            db.execute(
                "INSERT INTO sighting_members VALUES(?,?,?,?,?)",
                (r["detection_id"], key, reason, runner, score),
            )

    end = occ["scheduled_at"] + occ["duration_s"] / occ["speed"]
    db.execute(
        """UPDATE sightings SET closed=1 WHERE closed=0 AND run=? AND source=?
        AND (continuity!=? OR last+1<? OR tail_sequence<? OR continuity=?)""",
        (run, camera, continuity, end, occ["sequence"], occ["occurrence_id"]),
    )


def advance(db):
    occ = db.execute("""SELECT o.* FROM feed.occurrences o WHERE state IN ('complete','recorded')
        AND NOT EXISTS(SELECT 1 FROM sighting_runs s WHERE s.occurrence_id=o.occurrence_id)
        AND NOT EXISTS(SELECT 1 FROM feed.occurrences earlier WHERE earlier.run_id=o.run_id
          AND earlier.source_id=o.source_id AND earlier.session_id=o.session_id AND earlier.sequence<o.sequence
          AND earlier.state NOT IN ('expired','expiring','failed')
          AND NOT EXISTS(SELECT 1 FROM sighting_runs s WHERE s.occurrence_id=earlier.occurrence_id))
        AND NOT EXISTS(SELECT 1 FROM feed.observations b LEFT JOIN decisions d USING(detection_id)
          WHERE b.occurrence_id=o.occurrence_id AND d.detection_id IS NULL)
        ORDER BY scheduled_at,sequence LIMIT 1""").fetchone()
    if occ is None:
        return False
    with db:
        if db.execute(
            "INSERT OR IGNORE INTO sighting_runs VALUES(?)", (occ["occurrence_id"],)
        ).rowcount:
            materialize(db, occ)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--review-queue",
        action="store_true",
        help="Show up to 50 recent candidates and withholding counts",
    )
    parser.add_argument("--approve", metavar="APPEARANCE")
    parser.add_argument(
        "--withheld",
        action="store_true",
        help="List recent unresolved observations and their saved reasons",
    )
    parser.add_argument("--person")
    parser.add_argument("--reviewer")
    parser.add_argument(
        "--representative", help="Exact detection inspected during review"
    )
    args = parser.parse_args()
    with core.connect() as db:
        db.executescript(SCHEMA)
        db.execute(
            "ATTACH DATABASE ? AS feed",
            ((core.ROOT / "feed.sqlite").as_uri() + "?mode=ro",),
        )
        if args.withheld:
            rows = db.execute("""SELECT m.detection_id,i.source_id,i.occurrence_id,i.frame_index,m.reason,d.version,
                d.reference_id,d.score,d.threshold,m.runner,m.runner_score FROM sighting_members m
                JOIN indexed i USING(detection_id) JOIN decisions d USING(detection_id)
                WHERE m.appearance IS NULL ORDER BY m.rowid DESC LIMIT 100""")
            print(json.dumps([dict(r) for r in rows], indent=2))
        elif args.review_queue:
            candidates = []
            for row in db.execute("""SELECT s.id,s.source,s.first,s.last,s.person,s.version,s.score,
                    s.representative,i.occurrence_id,i.frame_index FROM sightings s
                    JOIN indexed i ON i.detection_id=s.representative
                    WHERE reviewed_at IS NULL AND closed=1 ORDER BY last DESC LIMIT 50"""):
                candidates.append(
                    dict(
                        row,
                        review_url=f"/?view=recordings&occ={row['occurrence_id']}&frame={row['frame_index']}&face={row['representative']}",
                    )
                )
            counts = dict(
                db.execute(
                    "SELECT reason,count(*) FROM sighting_members GROUP BY reason"
                )
            )
            print(
                json.dumps(
                    dict(candidates=candidates, decision_counts=counts), indent=2
                )
            )
        elif args.approve:
            if not (args.person and args.reviewer and args.representative):
                parser.error("approval requires person, reviewer and representative")
            if (
                db.execute(
                    """UPDATE sightings SET reviewed_at=?,reviewed_by=? WHERE id=? AND person=?
                AND representative=? AND closed=1 AND reviewed_at IS NULL AND EXISTS(SELECT 1 FROM indexed WHERE detection_id=representative)""",
                    (
                        time.time(),
                        args.reviewer,
                        args.approve,
                        args.person,
                        args.representative,
                    ),
                ).rowcount
                != 1
            ):
                parser.error(
                    "unknown, changed, expired or previously reviewed appearance"
                )
        else:
            while advance(db):
                pass


if __name__ == "__main__":
    main()
