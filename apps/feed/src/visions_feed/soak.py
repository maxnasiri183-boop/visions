"""Observe the actual soak on disk; detect missing samples and unexplained source stalls."""

import json
import os
import sqlite3
import time
from .db import DATA, bounded_id, connect

INTERVAL = 10
STALL = 45


def atomic(target, value):
    tmp = target.with_suffix(".new")
    tmp.write_text(json.dumps(value, indent=2) + "\n")
    tmp.replace(target)


def capture(db, run):
    now = time.time()
    rr = db.execute("SELECT * FROM runs WHERE run_id=?", (run,)).fetchone()
    if not rr:
        raise RuntimeError("Waiting for feeder run")
    through = rr["stop_at"] if rr["stop_at"] is not None else now
    totals = dict(
        db.execute("SELECT * FROM run_totals WHERE run_id=?", (run,)).fetchone()
    )
    details = []
    for row in db.execute(
        """SELECT source_id,count(*) AS occurrences,sum(last_frame+1) AS durable_frames,
        sum(CASE WHEN state IN ('expired','complete','recorded') THEN frame_count-coalesce((SELECT frames FROM recording_skips s WHERE s.occurrence_id=occurrences.occurrence_id),0) ELSE 0 END) AS completed_expected,
        sum(CASE WHEN state IN ('expired','complete','recorded') THEN last_frame+1 ELSE 0 END) AS completed_actual,
        max(scheduled_at+duration_s/speed) AS scheduled_until,
        sum(CASE WHEN state NOT IN ('expired','complete','recorded') THEN 1 ELSE 0 END) AS outstanding
        FROM occurrences WHERE run_id=? AND scheduled_at<? GROUP BY source_id""",
        (run, through),
    ):
        item = dict(row)
        source = db.execute(
            "SELECT heartbeat_at,state,detail FROM service_state WHERE service=?",
            ("source:" + row["source_id"],),
        ).fetchone()
        item["source_health"] = dict(source) if source else None
        details.append(item)
    states = [dict(r) for r in db.execute("SELECT * FROM service_state")]
    pending = db.execute(
        """SELECT sum(ob.sql_visible_at IS NULL),sum(ob.search_state IS NULL) FROM observations ob
        JOIN occurrences oc USING(occurrence_id) WHERE run_id=? AND oc.scheduled_at<?""",
        (run, through),
    ).fetchone()
    expired = db.execute(
        """SELECT sum(missing_sql),sum(missing_identity),sum(identity_errors) FROM expiry_counts
        JOIN occurrences USING(occurrence_id) WHERE run_id=? AND scheduled_at<?""",
        (run, through),
    ).fetchone()
    return {
        "expired_missing_sql": expired[0] or 0,
        "expired_missing_identity": expired[1] or 0,
        "expired_identity_errors": expired[2] or 0,
        "at": now,
        "run": dict(rr),
        "totals": totals,
        "sources": details,
        "services": states,
        "pending_sql": pending[0] or 0,
        "pending_identity": pending[1] or 0,
        "free_bytes": __import__("shutil").disk_usage(DATA).free,
    }


def monitor():
    run = bounded_id(os.environ["FEED_RUN_ID"])
    root = DATA / "soaks" / run
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    summary_file = root / "status.json"
    prev = json.loads(summary_file.read_text()) if summary_file.exists() else {}
    alerts = prev.get("alerts", [])
    counters = prev.get("source_counters", {})
    last = prev.get("sample_at")
    samples = prev.get("samples", 0)
    db = connect()
    while True:
        try:
            snap = capture(db, run)
        except (sqlite3.Error, RuntimeError) as e:
            print(f"Waiting for soak state: {e}", flush=True)
            time.sleep(2)
            continue
        now = snap["at"]
        rr = snap["run"]
        current = set()
        if snap["free_bytes"] < int(
            os.getenv("FEED_MIN_FREE_BYTES", str(24 * 1024**3))
        ):
            current.add("disk_below_reserve")

        ended = rr["stop_at"] is not None and (now >= rr["stop_at"] or rr["state"] == "finished")
        if last is not None and now - last > INTERVAL * 3:
            current.add("unobserved_interval")
        if samples == 0 and now - rr["created_at"] > STALL:
            current.add("late_monitor_start")
        for src in snap["sources"]:
            count = src["durable_frames"]
            name = src["source_id"]
            counter = counters.setdefault(
                name, {"frames": count, "last_progress_at": now}
            )
            if counter["frames"] != count:
                counter.update(frames=count, last_progress_at=now)
            src["seconds_since_progress"] = now - counter["last_progress_at"]
            if (not ended or src["outstanding"]) and src[
                "seconds_since_progress"
            ] > STALL:
                reason = src["source_health"] or {}
                current.add(
                    f"{name}:controlled_source_outage"
                    if reason.get("detail") == "Controlled source interruption"
                    else f"{name}:unexplained_stall"
                )
        if not snap["sources"] and now - rr["created_at"] > STALL:
            current.add("no_sources")
        for server in snap["services"]:
            if server["service"] in (
                "feeder",
                "worker",
                "sql-observer",
                "search-observer",
                "retention",
            ):
                if now - server["heartbeat_at"] > STALL or server["state"] in (
                    "storage_guard",
                    "error",
                    "unavailable",
                    "waiting_backend",
                ):
                    current.add(server["service"] + ":unavailable")
        active = {a["type"] for a in alerts if a["end"] is None}
        for key in current - active:
            alerts.append({"type": key, "start": now, "end": None})
            print(json.dumps({"soak": run, "alert": key, "at": now}), flush=True)
        for item in alerts:
            if item["end"] is None and item["type"] not in current:
                item["end"] = now
        samples += 1
        with (root / "samples.jsonl").open("a") as f:
            f.write(json.dumps(snap, separators=(",", ":")) + "\n")
        pending = sum(s["outstanding"] for s in snap["sources"])
        elapsed = now - rr["created_at"]
        outcome = "running"
        if ended and (
            not pending
            and not snap["pending_sql"]
            and not snap["pending_identity"]
            or now - rr["stop_at"] > 300
        ):
            accounted = bool(snap["sources"]) and all(
                s["completed_expected"] == s["completed_actual"]
                and rr["stop_at"] - s["scheduled_until"] < 2
                for s in snap["sources"]
            )
            okay = (
                accounted
                and not pending
                and not snap["pending_sql"]
                and not snap["pending_identity"]
                and not snap["expired_missing_sql"]
                and not snap["expired_missing_identity"]
                and not snap["expired_identity_errors"]
                and not any(
                    a["type"].endswith(
                        (
                            "unexplained_stall",
                            "unobserved_interval",
                            "late_monitor_start",
                            "no_sources",
                        )
                    )
                    for a in alerts
                )
            )
            outcome = "pass" if okay else "failed"
        status = {
            "run_id": run,
            "outcome": outcome,
            "sample_at": now,
            "samples": samples,
            "source_counters": counters,
            "started_at": rr["created_at"],
            "stop_at": rr["stop_at"],
            "elapsed_seconds": elapsed,
            "alerts": alerts,
            "expired_missing_sql": snap["expired_missing_sql"],
            "expired_missing_identity": snap["expired_missing_identity"],
            "expired_identity_errors": snap["expired_identity_errors"],
            "frame_accounting": snap["sources"],
            "totals": snap["totals"],
            "free_bytes": snap["free_bytes"],
            "pending_sql": snap["pending_sql"],
            "pending_identity": snap["pending_identity"],
            "sample_interval_seconds": INTERVAL,
            "stall_limit_seconds": STALL,
        }
        atomic(summary_file, status)
        if outcome != "running":
            atomic(root / "report.json", status)
            print(
                json.dumps({"soak": run, "outcome": outcome, "samples": samples}),
                flush=True,
            )
            while True:
                time.sleep(60)
        last = now
        time.sleep(INTERVAL)


if __name__ == "__main__":
    monitor()
