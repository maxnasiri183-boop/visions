"""Keep a completed private recording for a saved gallery version and print its review links."""

import argparse
from contextlib import closing
import fcntl
import json
import os
from pathlib import Path
import sqlite3
from tempfile import NamedTemporaryFile
from urllib.parse import urlencode


def pin(root, source, asset, person, version, pair):
    path = root / "pinned.json"
    with (
        (root / ".review-pins.lock").open("a") as lock,
        closing(
            sqlite3.connect((root / "feed.sqlite").as_uri() + "?mode=ro", uri=True)
        ) as db,
    ):
        fcntl.flock(lock, fcntl.LOCK_EX)
        db.row_factory = sqlite3.Row
        db.execute(
            "ATTACH DATABASE ? AS ident",
            ((root / "identity.sqlite").as_uri() + "?mode=ro",),
        )
        pins = json.loads(path.read_text()) if path.exists() else []
        recent = db.execute(
            """SELECT occurrence_id FROM occurrences WHERE source_id=? AND asset_id=?
            AND state='complete' ORDER BY finished_at DESC LIMIT 100""",
            (source, asset),
        )
        for oid in dict.fromkeys([*pins, *(r[0] for r in recent)]):
            occ = db.execute(
                """SELECT asset_id FROM occurrences WHERE occurrence_id=? AND source_id=?
                        AND asset_id=? AND state='complete'""",
                (oid, source, asset),
            ).fetchone()
            if not occ:
                continue
            rows = db.execute(
                """SELECT o.detection_id,o.frame_index,d.state,r.person_id,d.score,d.threshold,d.reference_id
                    FROM observations o JOIN ident.decisions d USING(detection_id) LEFT JOIN ident.refs r USING(reference_id)
                    WHERE o.occurrence_id=? AND d.version=? AND o.sql_visible_at IS NOT NULL ORDER BY o.frame_index""",
                (oid, version),
            ).fetchall()
            known = next(
                (
                    r
                    for r in rows
                    if r["state"] == "matched" and r["person_id"] == person
                ),
                None,
            )
            if not known:
                continue
            unknown = next(
                (
                    r
                    for r in rows
                    if r["state"] == "unknown"
                    and r["frame_index"] == known["frame_index"]
                ),
                None,
            )
            empty = db.execute(
                "SELECT frame_index FROM frames WHERE occurrence_id=? AND face_count=0 ORDER BY frame_index LIMIT 1",
                (oid,),
            ).fetchone()
            if pair and not (empty and unknown):
                continue
            if oid not in pins:
                if len(pins) >= 8:
                    raise ValueError("All eight retention slots are in use")
                with NamedTemporaryFile(dir=root, mode="w", delete=False) as stream:
                    json.dump(pins + [oid], stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                Path(stream.name).replace(path)
            base = "http://127.0.0.1:8092/?"

            def link(row):
                return base + urlencode(
                    dict(
                        view="recordings",
                        occ=oid,
                        frame=row["frame_index"],
                        face=row["detection_id"],
                    )
                )

            return dict(
                occurrence_id=oid,
                source=source,
                asset=asset,
                gallery_version=version,
                gallery=base
                + urlencode(dict(view="gallery", version=version, person=person)),
                portrait="http://127.0.0.1:8092/api/identity/references/"
                + known["reference_id"]
                + "/portrait",
                known=link(known),
                known_score=known["score"],
                threshold=known["threshold"],
                unknown=link(unknown) if unknown else None,
                unknown_score=unknown["score"] if unknown else None,
                empty=base
                + urlencode(
                    dict(view="recordings", occ=oid, frame=empty["frame_index"])
                )
                if empty
                else None,
            )
        raise ValueError(
            "No completed recording with the requested saved decisions and SQL evidence"
        )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=Path("private/operator"))
    for key in ("source", "asset", "person"):
        p.add_argument("--" + key, required=True)
    p.add_argument("--version", required=True, type=int)
    p.add_argument("--require-unknown-and-empty", action="store_true")
    args = p.parse_args()
    print(
        json.dumps(
            pin(
                args.root.resolve(),
                args.source,
                args.asset,
                args.person,
                args.version,
                args.require_unknown_and_empty,
            ),
            indent=2,
        )
    )
