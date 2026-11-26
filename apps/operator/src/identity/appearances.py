"""Playable evidence for a reference, drawn exclusively from saved worker decisions."""
import json
from . import core


def for_reference(reference, version):
    root = core.ROOT
    with core.connect() as db:
        if not db.execute("SELECT 1 FROM refs WHERE reference_id=?", (reference,)).fetchone():
            raise KeyError("Reference not found")
        db.execute("ATTACH DATABASE ? AS feed", ((root / "feed.sqlite").as_uri() + "?mode=ro",))
        # Walk retained recordings, starting with pinned examples with stable deep links.
        pins = json.loads((root / "pinned.json").read_text()) if (root / "pinned.json").exists() else []
        latest = db.execute("""SELECT occurrence_id FROM feed.occurrences WHERE state IN ('complete','processing')
                             AND last_frame>=0 ORDER BY scheduled_at DESC LIMIT 128""")
        occurrences = dict.fromkeys([*pins, *(r[0] for r in latest)])
        results, assets = [], set()
        for oid in occurrences:
            occ = db.execute("SELECT * FROM feed.occurrences WHERE occurrence_id=? AND state!='expired'", (oid,)).fetchone()
            if not occ or occ["asset_sha256"] in assets:
                continue
            row = db.execute("""SELECT i.detection_id,i.frame_index,i.media_pts_s,d.version,d.score
                FROM indexed i JOIN decisions d USING(detection_id)
                WHERE i.occurrence_id=? AND d.state='matched' AND d.reference_id=?
                AND (? IS NULL OR d.version=?) ORDER BY i.frame_index LIMIT 1""",
                (oid,reference,version,version)).fetchone()
            if row:
                assets.add(occ["asset_sha256"])
                results.append(dict(row, occurrence_id=oid, asset_id=occ["asset_id"], source_id=occ["source_id"]))
            if len(results) == 8:
                break
        return {"items": results}
