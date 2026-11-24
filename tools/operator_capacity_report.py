"""Aggregate one completed private operator capacity trial without exporting face data."""

from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
import json
import math
import re
import sqlite3


def distribution(values):
    data = sorted(x for x in values if x is not None and math.isfinite(x))
    if not data:
        return dict(n=0, p50=None, p95=None, max=None)
    return dict(
        n=len(data),
        p50=data[math.ceil(len(data) * 0.5) - 1],
        p95=data[math.ceil(len(data) * 0.95) - 1],
        max=data[-1],
    )


def summarize(root, n, catalog, labels, samples, searches, cycles):
    with closing(sqlite3.connect(root / "feed.sqlite")) as db:
        db.row_factory = sqlite3.Row
        db.execute("ATTACH ? AS ident", (str(root / "identity.sqlite"),))
        jobs = db.execute(
            "SELECT * FROM occurrences WHERE source_id LIKE 'qa-%'"
        ).fetchall()
        frames = db.execute("""SELECT f.*,o.source_id,o.asset_id FROM frames f JOIN occurrences o USING(occurrence_id)
            WHERE o.source_id LIKE 'qa-%'""").fetchall()
        rows = db.execute("""SELECT obs.*,i.state AS index_state,i.ingested_at,i.indexed_at AS identity_at,
            i.align_ms,i.embed_ms,i.lookup_ms,length(i.embedding) embedding_bytes,d.state decision,
            d.version decision_version,r.person_id AS winner FROM observations obs
            JOIN ident.indexed i USING(detection_id) JOIN ident.decisions d USING(detection_id)
            LEFT JOIN ident.refs r USING(reference_id) WHERE i.source_id LIKE 'qa-%'""").fetchall()
        warm = db.execute("""SELECT count(*) frames, sum(f.face_count) detections FROM frames f
            JOIN occurrences o USING(occurrence_id) WHERE o.source_id LIKE 'warm-%'""").fetchone()
        gallery = db.execute(
            "SELECT * FROM ident.versions ORDER BY version DESC LIMIT 1"
        ).fetchone()
        last_warm = db.execute("""SELECT max(last_at) FROM (
            SELECT max(f.indexed_at) last_at FROM frames f JOIN occurrences o USING(occurrence_id) WHERE o.source_id LIKE 'warm-%'
            UNION ALL SELECT max(v.sql_visible_at) FROM observations v JOIN occurrences o USING(occurrence_id) WHERE o.source_id LIKE 'warm-%'
            UNION ALL SELECT max(indexed_at) FROM ident.indexed WHERE source_id LIKE 'warm-%')""").fetchone()[
            0
        ]
        members = {
            r[0]
            for r in db.execute(
                "SELECT person_id FROM ident.refs JOIN ident.membership USING(reference_id) WHERE version=?",
                (gallery["version"],),
            )
        }
    expected_jobs = n * len(catalog) * cycles
    assert len(jobs) == expected_jobs and all(j["state"] == "complete" for j in jobs)
    truth = {c["asset_id"]: labels[c["path"]] for c in catalog}
    expected_frames = sum(truth[j["asset_id"]]["frames"] for j in jobs)
    assert len(frames) == expected_frames and sum(
        f["face_count"] for f in frames
    ) == len(rows)
    assert len({r["detection_id"] for r in rows}) == len(rows)
    stage = defaultdict(list)
    quality = Counter()
    by_resolution = Counter()
    by_source = Counter()
    contexts = {}
    t0 = min(j["scheduled_at"] for j in jobs)
    if last_warm is None or last_warm > t0:
        raise ValueError("Warm-up activity overlapped the measurement window")
    media = sum(j["duration_s"] for j in jobs) / n
    for f in frames:
        stats = json.loads(f["stats_json"])
        assert (
            stats["ran_inference"]
            and stats["index"] == f["frame_index"]
            and stats["source"] == f["occurrence_id"]
        )
        by_resolution[f"{f['width']}x{f['height']}"] += 1
        by_source[f["source_id"]] += 1
        for k in (
            "decode_ms",
            "preprocess_ms",
            "inference_ms",
            "crop_upload_ms",
            "manifest_io_ms",
            "kafka_publish_ms",
            "lag_ms",
        ):
            if f["face_count"] or k not in ("crop_upload_ms", "kafka_publish_ms"):
                stage[k].append(stats[k])
        stage["persist_after_publish_ms"].append(
            (f["indexed_at"] - stats["wall_time"]) * 1000
        )
        people = truth[f["asset_id"]]["people"]
        quality["labeled_faces"] += len(people)
        quality["frames_exact_face_count"] += int(len(people) == f["face_count"])
        quality["count_shortfall"] += max(0, len(people) - f["face_count"])
        quality["count_excess"] += max(0, f["face_count"] - len(people))
        contexts[(f["occurrence_id"], f["frame_index"])] = (f, people)
    decode_log = root / "recognition-decode.jsonl"
    if decode_log.exists():
        for line in decode_log.read_text().splitlines():
            oid, idx, ms = json.loads(line)
            if (oid, idx) in contexts:
                stage["recognition_decode_ms"].append(ms)
    ready = 0
    by_detection = {}
    for row in rows:
        event = json.loads(row["event_json"])
        sql = json.loads(row["sql_row"])
        f, people = contexts[row["occurrence_id"], row["frame_index"]]
        assert (
            row["topic"] == "face.operator.v1"
            and row["latest_offset"] == row["kafka_offset"]
        )
        assert (
            event["source_id"] == f["occurrence_id"]
            and event["frame_index"] == f["frame_index"]
        )
        for k, v in event.items():
            assert v == (json.loads(sql[k]) if isinstance(v, list) else sql[k]), k
        assert row["sql_visible_at"] and row["decision_version"] == gallery["version"]
        stage["persist_to_sql_visibility_s"].append(
            row["sql_visible_at"] - f["indexed_at"]
        )
        stage["persist_to_recognition_s"].append(row["identity_at"] - f["indexed_at"])
        stage["sql_visibility_s"].append(row["sql_visible_at"] - row["ingested_at"])
        stage["decision_visibility_s"].append(row["identity_at"] - row["ingested_at"])
        for k in ("align_ms", "embed_ms", "lookup_ms"):
            if row[k] is not None:
                stage[k].append(row[k])
        if row["index_state"] == "ready":
            assert (
                row["embedding_bytes"] == 512 and row["search_visible_at"] is not None
            )
            ready += 1
            stage["search_visibility_s"].append(
                row["search_visible_at"] - row["ingested_at"]
            )
        quality["state_" + row["decision"]] += 1
        x, _, w, _ = event["bbox_xywh"]
        who = (
            people[min(len(people) - 1, int((x + w / 2) / f["width"] * len(people)))]
            if people
            else None
        )
        by_detection[row["detection_id"]] = who
        if who:
            if who in members:
                quality["enrolled_observations"] += 1
                quality["correct_match"] += int(
                    row["decision"] == "matched" and row["winner"] == who
                )
            else:
                quality["unenrolled_observations"] += 1
                quality["correct_unknown"] += int(row["decision"] == "unknown")
            quality["wrong_positive"] += int(
                row["decision"] == "matched" and row["winner"] != who
            )
    upload = json.loads((root / "probe-label.json").read_text())
    hit = [s for s in searches if s["hits"]]
    stage["filtered_search_api_ms"] = [s["elapsed_ms"] for s in searches]
    quality["query_hits"] = len(hit)
    quality["correct_top_group_with_hits"] = sum(
        by_detection.get(s["top_detection"]) == upload for s in hit
    )
    tend = max(
        max(f["indexed_at"] for f in frames),
        max(r["sql_visible_at"] for r in rows),
        max(r["search_visible_at"] or r["identity_at"] for r in rows),
    )
    decode_seconds = max(f["indexed_at"] for f in frames) - t0
    active = [s for s in samples if t0 <= s["at"] <= tend]
    cpu = []
    for a, b in zip(active, active[1:]):
        delta = b["cpu_total"] - a["cpu_total"]
        if delta:
            cpu.append(100 * (1 - (b["cpu_idle"] - a["cpu_idle"]) / delta))
    dist = {k: distribution(v) for k, v in stage.items()}
    state = Counter(r["decision"] for r in rows)
    pace = len(frames) / decode_seconds
    target = len(frames) / media
    gates = dict(
        feed_keeps_up=pace >= target * 0.98,
        lag_under_one_second=dist["lag_ms"]["p95"] < 1000,
        sql_under_ten_seconds=dist["sql_visibility_s"]["p95"] < 10,
        searchable_under_five_seconds=bool(ready)
        and dist["search_visibility_s"]["p95"] < 5,
        no_errors=not state["error"],
    )
    return dict(
        streams=n,
        frames=len(frames),
        detections=len(rows),
        searchable=ready,
        source_rate_range_fps=sorted(
            {truth[j["asset_id"]]["frame_rate"] for j in jobs}
        ),
        media_seconds_per_stream=media,
        resolution_frame_counts=by_resolution,
        warmup_frames=warm["frames"],
        warmup_detections=warm["detections"],
        frames_per_stream=[by_source[s] for s in sorted(by_source)],
        target_fps=target,
        feed_rate_fps=pace,
        full_rate_fps=len(frames) / (tend - t0),
        window_start_utc=datetime.fromtimestamp(t0, timezone.utc).isoformat(),
        window_end_utc=datetime.fromtimestamp(tend, timezone.utc).isoformat(),
        feed_duration_s=decode_seconds,
        full_duration_s=tend - t0,
        drain_after_last_persisted_frame_s=tend - t0 - decode_seconds,
        dropped_frames=expected_frames - len(frames),
        sampled_out=0,
        retries=sum(j["attempts"] - 1 for j in jobs),
        stage_distributions=dist,
        quality=quality,
        reference_count=len(members),
        gallery_version=gallery["version"],
        recognition_threshold=gallery["threshold"],
        search_threshold=gallery["search_threshold"],
        playout_speed=1,
        detector_batch_size=1,
        hardware_window_samples=len(active),
        host=dict(
            cpu_percent=distribution(cpu),
            used_ram_mib=distribution([s["ram_used_mib"] for s in active]),
            gpu_percent=distribution([s["gpu_pct"] for s in active]),
            vram_mib=distribution([s["vram_mib"] for s in active]),
            min_disk_free_bytes=min(s["disk_free_bytes"] for s in active),
        ),
        peak_backlog={
            k: max(s[k] for s in active)
            for k in ("pending_identity", "pending_sql", "queued")
        },
        query_candidate_counts=[s["candidates"] for s in searches],
        gates=gates,
        meets_target=all(gates.values()),
    )


def runtime_evidence(root, result, background_db=None):
    """Record per-JVM kernel checks and any live co-located pilot feed."""
    logs = (root / "feed-worker.log").read_text()
    profile = re.findall(
        r"ort_profile_cuda_kernels=(\d+) ort_profile_cpu_kernels=(\d+)", logs
    )
    requested = result.get("detector_provider", "cuda")
    providers = re.findall(r"onnxruntime_provider_requested=(cuda|cpu)", logs)
    if not providers or set(providers) != {requested}:
        raise ValueError("Unexpected ONNX provider")
    if requested == "cuda" and (
        len(profile) < result["streams"]
        or any(int(cuda) < 1 or int(cpu) > 0 for cuda, cpu in profile)
    ):
        raise ValueError("Incomplete CUDA kernel verification")
    if requested == "cpu" and (profile or result.get("worker_gpu_devices") != 0):
        raise ValueError("CPU execution did not have confirmed device isolation")
    result["detector_provider"] = requested
    result["kernel_checks"] = [
        {"cuda": int(cuda), "cpu": int(cpu)} for cuda, cpu in profile
    ]
    if background_db:
        start = datetime.fromisoformat(result["window_start_utc"]).timestamp()
        end = datetime.fromisoformat(result["window_end_utc"]).timestamp()
        with closing(
            sqlite3.connect(background_db.as_uri() + "?mode=ro", uri=True, timeout=8)
        ) as db:
            background = db.execute(
                "SELECT count(*),sum(face_count) FROM frames WHERE indexed_at BETWEEN ? AND ?",
                (start, end),
            ).fetchone()
        result["concurrent_pilot"] = {
            "frames": background[0],
            "detections": background[1] or 0,
            "fps": background[0] / (end - start),
        }
    return result
