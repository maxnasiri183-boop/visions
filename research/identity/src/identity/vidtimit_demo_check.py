"""Check the staged gallery against pinned, previously processed demonstrations read-only."""

import argparse
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from .core import dot


def check(candidate, pilot):
    config = json.loads((candidate / "frozen-calibration.json").read_text())
    aliases = json.loads((candidate / "private-alias-crosswalk.json").read_text())
    truth = {row["person_id"]: who for who, row in aliases.items()}
    pair = json.loads((pilot / "research/paired-passage.json").read_text())["people"]
    if len(pair) != 2:
        raise ValueError("Expected the original labeled two-person passage")
    available = json.loads((pilot / "pinned.json").read_text())
    catalog = {
        r["sha256"]: r
        for filename in ("stream", "stream-extension")
        for r in json.loads((pilot / "research" / (filename + ".json")).read_text())
    }
    metrics = Counter()
    examples = []
    duo = Counter()
    with closing(
        sqlite3.connect((candidate / "identity.sqlite").as_uri() + "?mode=ro", uri=True)
    ) as proposed:
        refs = proposed.execute("""SELECT person_id,embedding FROM refs JOIN membership USING(reference_id)
                                 WHERE version=(SELECT max(version) FROM versions)""").fetchall()
        refs = [(truth[p], emb) for p, emb in refs]
        enrolled_keys = {p for p, _ in refs}
        if len(set(pair) & enrolled_keys) != 1:
            raise ValueError(
                "Expected one enrolled and one unenrolled participant in the pair"
            )
    with closing(
        sqlite3.connect((pilot / "identity.sqlite").as_uri() + "?mode=ro", uri=True)
    ) as db:
        db.row_factory = sqlite3.Row
        db.execute(
            "ATTACH DATABASE ? AS feed",
            ((pilot / "feed.sqlite").as_uri() + "?mode=ro",),
        )
        version_before = db.execute("SELECT max(version) FROM versions").fetchone()[0]
        for oid in available:
            occ = db.execute(
                "SELECT asset_sha256,asset_id FROM feed.occurrences WHERE occurrence_id=?",
                (oid,),
            ).fetchone()
            if not occ or occ["asset_sha256"] not in catalog:
                continue
            people = catalog[occ["asset_sha256"]]["people"]
            rows = db.execute(
                """SELECT i.detection_id,i.state,i.frame_index,i.embedding,i.bbox,f.width,d.state previous,d.version,
                   r.person_id old_winner FROM indexed i JOIN feed.frames f USING(occurrence_id,frame_index)
                   JOIN decisions d USING(detection_id) LEFT JOIN refs r USING(reference_id) WHERE i.occurrence_id=?""",
                (oid,),
            ).fetchall()
            metrics["occurrences"] += 1
            for row in rows:
                metrics["observations"] += 1
                if row["state"] != "ready":
                    metrics["quality_or_unavailable"] += 1
                    continue
                bbox = json.loads(row["bbox"])
                who = (
                    people[
                        int(
                            len(people) == 2
                            and bbox[0] + bbox[2] / 2 >= row["width"] / 2
                        )
                    ]
                    if people
                    else None
                )
                score, winner = max(
                    (dot(row["embedding"], embedding), person)
                    for person, embedding in refs
                )
                match = winner if score >= config["threshold"] else None
                enrolled = who in enrolled_keys
                metrics["enrolled" if enrolled else "unenrolled"] += 1
                metrics[
                    "candidate_correct_known"
                    if enrolled
                    else "candidate_correct_unknown"
                ] += int(match == who if enrolled else match is None)
                metrics["candidate_false_match"] += int(
                    match is not None and match != who
                )
                if people == pair:
                    if not enrolled:
                        duo["recorded_unknown_subject"] += 1
                        duo["candidate_unknown"] += int(match is None)
                    else:
                        duo["recorded_known_subject"] += 1
                        duo["candidate_correct"] += int(match == who)
                    if (
                        not enrolled and match is None and row["previous"] == "unknown"
                    ) or (enrolled and match == who and row["previous"] == "matched"):
                        examples.append(
                            {
                                "occurrence": oid,
                                "frame": row["frame_index"],
                                "id": row["detection_id"],
                                "known": enrolled,
                                "original_version": row["version"],
                            }
                        )
    if not any(e["known"] for e in examples) or not any(
        not e["known"] for e in examples
    ):
        raise ValueError("Could not verify both original pinned demo outcomes")
    return {
        "observed_live_version": version_before,
        "gallery_size": len(refs),
        "pinned_aggregate": dict(metrics),
        "pinned_duo": dict(duo),
        "matched_demo_cases": sum(e["known"] for e in examples),
        "unknown_demo_cases": sum(not e["known"] for e in examples),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--pilot-root", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(check(args.candidate.resolve(), args.pilot_root.resolve()), indent=2)
    )
