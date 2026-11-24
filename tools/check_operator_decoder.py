"""Compare retained pixel bytes and lookup time for path-keyed and occurrence-keyed decoders."""

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

from operator_capacity_report import distribution


def compare(root, limit):
    os.environ["OPERATOR_ROOT"] = str(root)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from identity.worker import Decoder

    feed = root / "feed.sqlite"
    if (root / "evidence-feed.sqlite").exists():
        feed = root / "evidence-feed.sqlite"
    with closing(sqlite3.connect(feed.as_uri() + "?mode=ro", uri=True)) as db:
        ordered = db.execute(
            """SELECT u.occurrence_id,u.input_path,f.frame_index FROM observations o
            JOIN occurrences u USING(occurrence_id) JOIN frames f USING(occurrence_id,frame_index)
            WHERE u.source_id LIKE 'warm-%' ORDER BY o.rowid LIMIT ?""",
            (limit,),
        ).fetchall()
    if len({r[0] for r in ordered}) < 2:
        raise ValueError(
            "Select a multi-stream trial with interleaved warm-up observations"
        )
    digests = []
    results = {}
    for by in ("path", "occurrence"):
        decoder = Decoder()
        timings = []
        pixels = hashlib.sha256()
        for occurrence, path, index in ordered:
            started = time.monotonic()
            image = decoder.frame(path if by == "path" else occurrence, path, index)
            timings.append((time.monotonic() - started) * 1000)
            pixels.update(memoryview(image))
        results[by] = dict(decode_ms=distribution(timings), total_ms=sum(timings))
        digests.append(pixels.digest())
    results["identical_pixels"] = digests[0] == digests[1]
    if not results["identical_pixels"]:
        raise ValueError("Decoded pixels differ")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trial", type=Path)
    parser.add_argument("--limit", type=int, default=70)
    args = parser.parse_args()
    print(json.dumps(compare(args.trial.resolve(), args.limit), indent=2))
