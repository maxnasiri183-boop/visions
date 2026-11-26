"""Compare appearance-level policies on independently labeled visits and frozen galleries."""

import argparse
import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from pathlib import Path

from . import core
from .evaluate import interval


def outcome(samples, threshold, margin, agreement):
    accepted = defaultdict(list)
    for at, ranked in samples:
        if not ranked:
            continue
        person, score = ranked[0]
        if score >= threshold and (len(ranked) == 1 or score - ranked[1][1] >= margin):
            accepted[person].append(at)
    if len(accepted) != 1:
        return None
    person, times = next(iter(accepted.items()))
    return person if not agreement or max(times) - min(times) >= 1 else None


def metrics(trials, people, cutoff, margin, agreement):
    known = unknown = wrong = shown = correct = false_unknown = 0
    absent_subjects = defaultdict(bool)
    known_subjects = defaultdict(bool)
    for row, samples in trials:
        truth = row["person"]
        found = outcome(samples, cutoff, margin, agreement)
        shown += found is not None
        wrong += found is not None and found != truth
        if truth in people:
            known += 1
            correct += found == truth
            known_subjects[truth] |= found != truth
        else:
            unknown += 1
            false_unknown += found is not None
            absent_subjects[truth] |= found is not None
    return dict(
        shown=shown,
        wrong_shown=wrong,
        enrolled_visits=known,
        correct=correct,
        enrolled_missed_or_withheld=known - correct,
        unenrolled_visits=unknown,
        false_accepts=false_unknown,
        unknown_subjects=len(absent_subjects),
        unknown_subject_any_false_ci95=interval(
            sum(absent_subjects.values()), len(absent_subjects)
        ),
        known_subject_any_miss_ci95=interval(
            sum(known_subjects.values()), len(known_subjects)
        ),
    )


def evaluate(db, labels, version):
    v = db.execute("SELECT * FROM versions WHERE version=?", (version,)).fetchone()
    if not v:
        raise ValueError("Unknown gallery version")
    refs = db.execute(
        "SELECT * FROM refs JOIN membership USING(reference_id) WHERE version=?",
        (version,),
    ).fetchall()
    people = {r["person_id"] for r in refs}
    roles = {"calibration": [], "evaluation": []}
    sessions = {}
    assets = {}
    used = set()
    enrollment_sessions = {r["session_id"] for r in refs}
    for visit in labels:
        role = visit["split"]
        session = visit["session"]
        if (
            role not in roles
            or not visit["person"]
            or session in enrollment_sessions
            or sessions.setdefault(session, role) != role
        ):
            raise ValueError("Invalid label or overlapping capture sessions")
        samples = []
        for did in visit["detections"]:
            if did in used:
                raise ValueError("A detection belongs to two labeled visits")
            used.add(did)
            row = db.execute(
                """SELECT i.embedding,o.scheduled_at+i.media_pts_s/o.speed at,o.asset_sha256,
                  f.model,f.preprocessing FROM indexed i JOIN feed.occurrences o USING(occurrence_id)
                  JOIN observation_features f USING(detection_id) WHERE detection_id=?""",
                (did,),
            ).fetchone()
            if (
                not row
                or row["model"] != v["model"]
                or row["preprocessing"] != v["preprocessing"]
            ):
                raise ValueError("Missing observation or incompatible feature lineage")
            if assets.setdefault(row["asset_sha256"], role) != role:
                raise ValueError("The same recorded asset appears in both splits")
            scores = {}
            if row["embedding"]:
                for ref in refs:
                    p = ref["person_id"]
                    scores[p] = max(
                        scores.get(p, -1), core.dot(row["embedding"], ref["embedding"])
                    )
            samples.append(
                (row["at"], sorted(scores.items(), key=lambda pair: -pair[1]))
            )
        roles[role].append((visit, samples))
    if not all(roles.values()):
        raise ValueError("Both calibration and evaluation visits are required")
    cutoff = v["threshold"]
    for trial, samples in roles["calibration"]:
        for _, ranked in samples:
            if ranked and ranked[0][0] != trial["person"]:
                cutoff = max(cutoff, ranked[0][1] + 1e-6)
    variants = [
        ("published", v["threshold"], 0, False),
        ("calibrated", cutoff, 0, False),
        ("distinct_person_margin", cutoff, 0.05, False),
        ("one_second_agreement", cutoff, 0.05, True),
    ]
    return dict(
        version=version,
        gallery_people=len(people),
        results=[
            dict(
                policy=name,
                threshold=t,
                margin=m,
                **metrics(roles["evaluation"], people, t, m, a),
            )
            for name, t, m, a in variants
        ],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("labels", type=Path)
    parser.add_argument("versions", nargs="+", type=int)
    args = parser.parse_args()
    with closing(
        sqlite3.connect((core.ROOT / "identity.sqlite").as_uri() + "?mode=ro", uri=True)
    ) as db:
        db.row_factory = sqlite3.Row
        db.execute(
            "ATTACH DATABASE ? AS feed",
            ((core.ROOT / "feed.sqlite").as_uri() + "?mode=ro",),
        )
        labels = json.loads(args.labels.read_text())
        print(json.dumps([evaluate(db, labels, v) for v in args.versions], indent=2))


if __name__ == "__main__":
    main()
