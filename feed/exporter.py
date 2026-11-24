"""Measured operator-feed health on an internal-only HTTP endpoint."""

import json
import math
import os
import shutil
import sqlite3
import threading
import time
from collections import defaultdict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from db import DATA, DB

WINDOW = 300
STAGES = (
    "decode_ms",
    "preprocess_ms",
    "inference_ms",
    "crop_upload_ms",
    "manifest_io_ms",
    "kafka_publish_ms",
)
IDENTITY_STAGES = ("align_ms", "embed_ms", "lookup_ms")
SNAPSHOT = None


def reader(path):
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
    db.row_factory = sqlite3.Row
    return db


def dist(values):
    values = sorted(v for v in values if v is not None and math.isfinite(v))
    return {
        "count": len(values),
        "p50": values[math.ceil(0.5 * len(values)) - 1] if values else None,
        "p95": values[math.ceil(0.95 * len(values)) - 1] if values else None,
    }


def routine_load():
    from backend import sql

    try:
        with sql() as db, db.cursor() as c:
            c.execute("USE visions_operator")
            c.execute("SHOW ROUTINE LOAD FOR load_detections")
            job = c.fetchone()
        stats = json.loads(job["Statistic"])
        lag = json.loads(job["OffsetLag"])
        return {
            "available": True,
            "state": job["State"],
            "progress": json.loads(job["Progress"]),
            "lag": sum(int(x) for x in lag.values() if str(x).isdigit()),
            "rejected": int(stats["errorRows"]),
            "loaded": int(stats["loadedRows"]),
            "last_error": job["ReasonOfStateChanged"] or "",
        }
    except Exception as error:
        return {
            "available": False,
            "state": "unavailable",
            "last_error": type(error).__name__,
        }


def snapshot():
    run = os.environ["FEED_RUN_ID"]
    now = time.time()
    cutoff = now - WINDOW
    result = {
        "run_id": run,
        "observed_at": now,
        "window_seconds": WINDOW,
        "rates_window_seconds": 5,
        "disk_free_bytes": shutil.disk_usage(DATA).free,
        "retention_seconds": int(os.environ.get("FEED_RETENTION_SECONDS", 3600)),
    }
    stages = defaultdict(list)
    with reader(DB) as db:
        db.execute("BEGIN")
        result["services"] = [
            dict(r) for r in db.execute("SELECT * FROM service_state")
        ]
        result["run"] = dict(
            db.execute("SELECT * FROM runs WHERE run_id=?", (run,)).fetchone() or {}
        )
        result["totals"] = dict(
            db.execute("SELECT * FROM run_totals WHERE run_id=?", (run,)).fetchone()
            or {}
        )
        result["sources"] = []
        for row in db.execute(
            "SELECT source_id,max(sequence) FROM occurrences WHERE run_id=? GROUP BY source_id",
            (run,),
        ):
            source = row[0]
            active = db.execute(
                "SELECT * FROM occurrences WHERE run_id=? AND source_id=? "
                "ORDER BY (state='processing') DESC,sequence DESC LIMIT 1",
                (run, source),
            ).fetchone()
            queue = db.execute(
                "SELECT count(*) AS count,min(scheduled_at) AS oldest,min(CASE WHEN state IN('queued','retry') THEN scheduled_at END) AS waiting FROM occurrences WHERE "
                "run_id=? AND source_id=? AND state IN('queued','processing','retry')",
                (run, source),
            ).fetchone()
            recent = db.execute(
                "SELECT f.indexed_at,f.face_count,f.media_pts_s,f.stats_json FROM frames f "
                "JOIN occurrences o USING(occurrence_id) WHERE o.run_id=? AND o.source_id=? AND f.indexed_at>=?",
                (run, source, cutoff),
            ).fetchall()
            latest = max((r["indexed_at"] for r in recent), default=None)
            rate = [r for r in recent if r["indexed_at"] >= now - 5]
            for r in recent:
                m = json.loads(r["stats_json"])
                for key in STAGES:
                    if m.get(key) is not None and (
                        r["face_count"]
                        or key not in ("crop_upload_ms", "kafka_publish_ms")
                    ):
                        stages[key[:-3]].append(m[key] / 1000)
                if m.get("lag_ms") is not None:
                    stages["playout_lag"].append(m["lag_ms"] / 1000)
            received = db.execute(
                "SELECT * FROM source_received WHERE source_id=? AND run_id=?",
                (source, run),
            ).fetchone()
            decoded = db.execute(
                "SELECT count(*) FROM receipts r JOIN occurrences o USING(occurrence_id) "
                "WHERE o.source_id=? AND o.run_id=? AND r.observed_at>=?",
                (source, run, now - 5),
            ).fetchone()[0]
            pulse = db.execute(
                "SELECT state,heartbeat_at FROM service_state WHERE service=?",
                ("source:" + source,),
            ).fetchone()
            completed = db.execute(
                "SELECT coalesce(sum(frame_count),0),coalesce(sum(frame_count-last_frame-1),0) "
                "FROM occurrences WHERE run_id=? AND source_id=? AND state IN('complete','expired')",
                (run, source),
            ).fetchone()
            result["sources"].append(
                {
                    "source_id": source,
                    "source_online": bool(
                        pulse
                        and pulse["state"] == "online"
                        and now - pulse["heartbeat_at"] < 10
                    ),
                    "source_heartbeat_at": pulse["heartbeat_at"] if pulse else None,
                    "latest_decoded_at": datetime.fromisoformat(
                        received["ingested_at"]
                    ).timestamp()
                    if received
                    else None,
                    "completed_expected_frames": completed[0],
                    "completed_missing_frames": completed[1],
                    "intentionally_skipped_frames": 0,
                    "observed_input_fps": None,
                    "decoded_fps": decoded / 5 if received else None,
                    "latest_decoded": dict(received) if received else None,
                    "input_measurement": "File playout target; independent ingress counter unavailable",
                    "target_fps": active["frame_count"]
                    * active["speed"]
                    / active["duration_s"],
                    "processed_fps": len(rate) / 5,
                    "inference_fps": sum(
                        bool(json.loads(r["stats_json"]).get("ran_inference"))
                        for r in rate
                    )
                    / 5,
                    "detections_per_second": sum(r["face_count"] for r in rate) / 5,
                    "latest_processed_at": latest,
                    "stale": latest is None or now - latest > 5,
                    "queue_occurrences": queue["count"],
                    "waiting_queue_age_seconds": max(0, now - queue["waiting"])
                    if queue["waiting"]
                    else 0,
                    "recent_playout_lateness_seconds": json.loads(
                        max(recent, key=lambda x: x["indexed_at"])["stats_json"]
                    ).get("lag_ms", 0)
                    / 1000
                    if recent
                    else None,
                    "oldest_queue_age_seconds": max(0, now - queue["oldest"])
                    if queue["oldest"]
                    else 0,
                    "intentional_sampling": "every frame",
                    "last_frame": active["last_frame"],
                    "expected_frames": active["frame_count"],
                }
            )
        # One wall-clock first-observed SQL timestamp per detection.
        for r in db.execute(
            "SELECT ob.sql_visible_at,f.ingested_at FROM observations ob JOIN occurrences o USING(occurrence_id) "
            "JOIN frames f ON ob.occurrence_id=f.occurrence_id AND ob.frame_index=f.frame_index "
            "WHERE o.run_id=? AND ob.sql_visible_at>=?",
            (run, cutoff),
        ):
            if r["ingested_at"]:
                stages["sql_visibility"].append(
                    r["sql_visible_at"]
                    - datetime.fromisoformat(r["ingested_at"]).timestamp()
                )
        for item in db.execute(
            "SELECT ob.search_visible_at,f.ingested_at FROM observations ob "
            "JOIN frames f ON ob.occurrence_id=f.occurrence_id AND ob.frame_index=f.frame_index "
            "JOIN occurrences o ON ob.occurrence_id=o.occurrence_id WHERE o.run_id=? AND ob.search_visible_at>=?",
            (run, cutoff),
        ):
            if item["ingested_at"]:
                stages["search_visibility"].append(
                    item["search_visible_at"]
                    - datetime.fromisoformat(item["ingested_at"]).timestamp()
                )
        result["pending_sql"] = db.execute(
            "SELECT count(*) FROM observations ob JOIN occurrences o USING(occurrence_id) "
            "WHERE run_id=? AND sql_visible_at IS NULL",
            (run,),
        ).fetchone()[0]
        identity_counts = db.execute(
            "SELECT sum(search_state IS NULL),sum(search_state='error'),"
            "sum(search_state='poor_quality'),max(search_visible_at) FROM observations JOIN occurrences USING(occurrence_id) WHERE run_id=?",
            (run,),
        ).fetchone()
        for key, value in zip(
            ("identity_pending", "identity_errors", "identity_quality_rejected"),
            identity_counts,
        ):
            result[key] = value or 0
        result["latest_searchable_at"] = identity_counts[3]
        expired = db.execute(
            "SELECT sum(detections),sum(missing_identity),sum(missing_sql) FROM expiry_counts "
            "JOIN occurrences USING(occurrence_id) WHERE run_id=?",
            (run,),
        ).fetchone()
        for key, value in zip(
            ("expired_detections", "expired_missing_identity", "expired_missing_sql"),
            expired,
        ):
            result[key] = value or 0
        result["recent_faults"] = [
            dict(r)
            for r in db.execute(
                "SELECT * FROM notices WHERE at>=? AND state IN"
                "('storage_guard','error','unavailable','source_outage','retry','waiting_backend') ORDER BY at DESC LIMIT 20",
                (cutoff,),
            )
        ]
        result["recoveries"] = [
            dict(r)
            for r in db.execute("SELECT * FROM recoveries ORDER BY at DESC LIMIT 12")
        ]
    result["routine_load"] = routine_load()
    result["identity_available"] = False
    try:
        with reader(DATA / "identity.sqlite") as ident:
            cols = {r[1] for r in ident.execute("PRAGMA table_info(indexed)")}
            measured = [x for x in IDENTITY_STAGES if x in cols]
            ident.execute("ATTACH DATABASE ? AS feed", (f"file:{DB}?mode=ro",))
            rows = ident.execute(
                "SELECT i.* FROM indexed i JOIN feed.occurrences o USING(occurrence_id) "
                "WHERE o.run_id=? AND i.indexed_at>=?",
                (run, cutoff),
            )
            for row in rows:
                for col in measured:
                    if row[col] is not None:
                        stages[col[:-3]].append(row[col] / 1000)
                if row["state"] == "ready" and row["embedding"] is not None:
                    if row["ingested_at"]:
                        stages["identity_index"].append(
                            row["indexed_at"] - row["ingested_at"]
                        )
            result["identity_available"] = True
    except (sqlite3.Error, KeyError, IndexError):
        pass
    result["stage_seconds"] = {
        key: dist(stages[key])
        for key in [s[:-3] for s in STAGES + IDENTITY_STAGES]
        + ["playout_lag", "sql_visibility", "identity_index", "search_visibility"]
    }
    status_path = DATA / "soaks" / run / "status.json"
    try:
        status = json.loads(status_path.read_text())
        result["soak"] = {
            "outcome": status["outcome"],
            "sample_at": status["sample_at"],
            "elapsed_seconds": status["elapsed_seconds"],
            "alerts": len(status["alerts"]),
            "open_alerts": sum(a["end"] is None for a in status["alerts"]),
        }
    except (OSError, ValueError, KeyError):
        result["soak"] = None
    result["disk_below_reserve"] = result["disk_free_bytes"] < int(
        os.getenv("FEED_MIN_FREE_BYTES", str(24 * 1024**3))
    )
    return result


def scrape(report):
    lines = []

    def emit(key, value, **tags):
        if value is None:
            return
        labels = ",".join(f"{k}={json.dumps(v)}" for k, v in tags.items())
        lines.append(
            f"visions_operator_{key}"
            + ("{" + labels + "}" if tags else "")
            + " "
            + str(float(value))
        )

    for source in report.get("sources", []):
        for key in (
            "source_online",
            "source_heartbeat_at",
            "latest_decoded_at",
            "completed_expected_frames",
            "completed_missing_frames",
            "intentionally_skipped_frames",
            "target_fps",
            "decoded_fps",
            "processed_fps",
            "inference_fps",
            "detections_per_second",
            "queue_occurrences",
            "waiting_queue_age_seconds",
            "recent_playout_lateness_seconds",
            "oldest_queue_age_seconds",
            "latest_processed_at",
            "stale",
        ):
            emit(key, source[key], source=source["source_id"])
    for key in (
        "identity_pending",
        "identity_errors",
        "identity_quality_rejected",
        "expired_detections",
        "expired_missing_identity",
        "expired_missing_sql",
        "disk_below_reserve",
        "pending_sql",
        "disk_free_bytes",
        "retention_seconds",
        "latest_searchable_at",
        "identity_available",
        "observed_at",
    ):
        emit(key, report.get(key))
    if report.get("soak"):
        soak = report["soak"]
        for field in ("sample_at", "elapsed_seconds", "alerts", "open_alerts"):
            emit("soak_" + field, soak[field])
    job = report.get("routine_load", {})
    emit("routine_load_available", job.get("available"))
    emit("routine_load_running", job.get("state") == "RUNNING")
    for field in ("lag", "rejected", "loaded"):
        emit("routine_load_" + field, job.get(field))
    for partition, offset in job.get("progress", {}).items():
        if str(offset).isdigit():
            emit("routine_partition_progress", int(offset), partition=partition)
    for stage, stats in report.get("stage_seconds", {}).items():
        emit("stage_samples", stats["count"], stage=stage)
        for q in ("p50", "p95"):
            emit("stage_seconds", stats[q], stage=stage, quantile=q)
    for key, val in report.get("totals", {}).items():
        if key != "run_id":
            emit(key + "_total", val)
    return ("\n".join(lines) + "\n").encode()


def collect():
    global SNAPSHOT
    while True:
        try:
            SNAPSHOT = snapshot()
        except Exception as e:
            print(
                f"Operator diagnostics unavailable: {type(e).__name__}: {e}", flush=True
            )
            SNAPSHOT = None
        time.sleep(4)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        report = SNAPSHOT
        if self.path not in ("/health", "/summary", "/metrics") or report is None:
            self.send_error(503 if report is None else 404)
            return
        body = (
            scrape(report)
            if self.path == "/metrics"
            else json.dumps(
                report if self.path == "/summary" else {"ok": True}
            ).encode()
        )
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/plain; version=0.0.4"
            if self.path == "/metrics"
            else "application/json",
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    threading.Thread(target=collect, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 9125), Handler).serve_forever()
