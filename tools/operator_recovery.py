"""Interrupt only the named operator feeder and worker, then verify recovery on real frames."""

import argparse
import json
import sqlite3
import subprocess
import time
from pathlib import Path


def test(root, run, compose):
    d = sqlite3.connect(root / "feed.sqlite", timeout=3)
    d.row_factory = sqlite3.Row

    def wait(query, params=(), seconds=90):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            r = d.execute(query, params).fetchone()
            if r:
                return dict(r)
            time.sleep(0.02)
        raise TimeoutError(query)

    def ctl(*args):
        subprocess.run(compose + list(args), check=True)

    feeder = "operator-feeder"
    worker = "operator-worker"
    ctl("stop", "-t", "1", feeder)
    seq = d.execute(
        "SELECT max(sequence) FROM occurrences WHERE run_id=?", (run,)
    ).fetchone()[0]
    offline = time.time()
    time.sleep(3)
    assert (
        d.execute(
            "SELECT max(sequence) FROM occurrences WHERE run_id=?", (run,)
        ).fetchone()[0]
        == seq
    )
    ctl("start", feeder)
    restart = time.time()
    new = wait(
        "SELECT sequence,occurrence_id FROM occurrences WHERE run_id=? AND sequence>? LIMIT 1",
        (run, seq),
    )
    resumed = time.time()
    # Stop an independently running detector while a positive frame from this occurrence is durable.
    row = wait(
        """SELECT o.occurrence_id FROM occurrences o JOIN frames f ON o.occurrence_id=f.occurrence_id
        AND o.last_frame=f.frame_index WHERE o.run_id=? AND o.state='processing' AND f.face_count>0
        AND o.last_frame>=2 AND o.last_frame<o.frame_count-10 ORDER BY o.scheduled_at LIMIT 1""",
        (run,),
    )
    oid = row["occurrence_id"]
    killed = time.time()
    ctl("stop", "-t", "0", worker)
    last = wait(
        "SELECT last_frame,frame_count,started_at FROM occurrences WHERE occurrence_id=?",
        (oid,),
    )
    ids = [
        dict(r)
        for r in d.execute(
            "SELECT detection_id,kafka_offset,latest_offset FROM observations "
            "WHERE occurrence_id=? AND frame_index=?",
            (oid, last["last_frame"]),
        )
    ]
    ctl("start", worker)
    started = time.time()
    checkpoint = wait(
        "SELECT * FROM recoveries WHERE occurrence_id=? AND first_recovered IS NOT NULL AND at>=?",
        (oid, killed),
    )
    first = time.time()
    done = wait(
        "SELECT finished_at FROM occurrences WHERE occurrence_id=? AND state='complete'",
        (oid,),
    )
    frames = [
        r[0]
        for r in d.execute(
            "SELECT frame_index FROM frames WHERE occurrence_id=? ORDER BY frame_index",
            (oid,),
        )
    ]
    assert frames == list(range(last["frame_count"])), "Unexpected frame gap"
    assert (
        checkpoint["previous_last"]
        == checkpoint["first_recovered"]
        == last["last_frame"]
    )
    replays = [
        dict(r)
        for r in d.execute(
            "SELECT detection_id,old_offset,new_offset FROM replay_events "
            "WHERE occurrence_id=? AND frame_index=? AND at>=?",
            (oid, last["last_frame"], killed),
        )
    ]
    assert ids and {x["detection_id"] for x in ids} == {
        x["detection_id"] for x in replays
    }
    assert all(x["new_offset"] > x["old_offset"] for x in replays)
    return {
        "run": run,
        "feeder": {
            "stopped_sequence": seq,
            "next_sequence": new["sequence"],
            "offline_s": restart - offline,
            "seconds_from_start_return_to_new_occurrence": resumed - restart,
        },
        "worker": {
            "occurrence_id": oid,
            "stop_requested_at": killed,
            "last_durable_index": last["last_frame"],
            "first_recovered_index": checkpoint["first_recovered"],
            "seconds_from_start_return_to_replay_observed": first - started,
            "seconds_from_stop_request_to_replay_observed": first - killed,
            "source_frame_count": last["frame_count"],
            "distinct_durable_frames": len(frames),
            "missing_frames": 0,
            "completed_at": done["finished_at"],
            "replays": replays,
        },
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--run", required=True)
    p.add_argument(
        "compose",
        nargs="+",
        help="A prepared Docker Compose command or wrapper, with project set",
    )
    a = p.parse_args()
    result = test(a.data, a.run, a.compose)
    target = a.data / ("recovery-" + str(int(time.time())) + ".json")
    target.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)
