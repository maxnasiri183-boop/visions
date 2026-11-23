"""Read-only diagnostics for the fixed-feed pipeline. All times are wall-clock seconds."""
from collections import deque
from contextlib import closing
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
from pathlib import Path
import shutil
import threading
import time
from urllib.request import ProxyHandler, build_opener

from flask import Blueprint, Response, jsonify, send_from_directory
from kafka import KafkaConsumer, TopicPartition
import pymysql

operations = Blueprint("operations", __name__)
STATIC = Path(__file__).resolve().parent / "static"
DATA = Path(os.getenv("PRIVATE", "private"))
WINDOW = 300
RATE_WINDOW = 5
STALE = 5
TOPIC = "face.detected.v1"
STAGES = ("decode_ms", "preprocess_ms", "inference_ms", "crop_upload_ms", "kafka_publish_ms", "manifest_io_ms")
_lock = threading.Lock()
_sample = {}
_cpu = None
_files = {}
_pool = ThreadPoolExecutor(max_workers=3)


def percentile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * q) - 1)]


def tail_records(path, cutoff):
    """Index complete JSON lines, retaining samples only inside the displayed window."""
    stat = path.stat()
    inode, offset, samples = _files.get(path, (None, 0, deque()))
    if inode != stat.st_ino or offset > stat.st_size:
        offset, samples = 0, deque()
    with path.open("rb") as source:
        source.seek(offset)
        while line := source.readline():
            if not line.endswith(b"\n"):
                break
            offset = source.tell()
            row = json.loads(line)
            if row["wall_time"] >= cutoff:
                samples.append(row)
    while samples and samples[0]["wall_time"] < cutoff:
        samples.popleft()
    _files[path] = (stat.st_ino, offset, samples)
    return samples


def workload(now):
    rows = []
    latencies = []
    found = set()
    errors = 0
    for path in DATA.glob("benchmarks/*/*.jsonl"):
        try:
            if path.stat().st_mtime < now - WINDOW:
                continue
            if path.name != "sql.jsonl" and not path.match("stream-*.jsonl"):
                continue
            found.add(path)
            samples = tail_records(path, now - WINDOW)
            if path.name == "sql.jsonl":
                latencies.extend(r["latency_s"] for r in samples if not r.get("warmup"))
                continue
            if not samples:
                continue
            last = samples[-1]
            complete = (path.parent / "summary.json").is_file()
            recent = [r for r in samples if r["wall_time"] >= last["wall_time"] - RATE_WINDOW
                      and r["warmup"] == last["warmup"]]
            span = recent[-1]["wall_time"] - recent[0]["wall_time"]
            stages = {}
            for key in STAGES:
                durations = [r[key] for r in recent if r.get(key) is not None
                             and (r["detections"] or key not in ("crop_upload_ms", "kafka_publish_ms"))]
                stages[key] = sum(durations) / len(durations) if durations else None
            fps = (len(recent) - 1) / span if span > 0 else None
            rows.append({
                "run": path.parent.name, "slot": last["stream"], "provider": last["provider"],
                "at": last["wall_time"], "active": not complete and now - last["wall_time"] <= STALE,
                "warmup": last["warmup"],
                "input_fps": last["input_fps"], "decoded_fps": fps, "inference_fps": fps,
                "detection_rate": sum(r["detections"] for r in recent[1:]) / span if span > 0 else None,
                "frames": last["index"] + 1, "skipped": 0, "dropped": 0,
                "lag_ms": last["lag_ms"], "media_s": last["media_pts_s"], "stages": stages,
            })
        except (OSError, ValueError, TypeError, KeyError):
            errors += 1
    for path in set(_files) - found:
        del _files[path]

    # Legacy and new single-observation reports both record one SQL visibility sample.
    latest = None
    for path in DATA.glob("review-*/report.json"):
        try:
            if path.stat().st_mtime < now - WINDOW and latest and path.stat().st_mtime < latest[0]:
                continue
            report = json.loads(path.read_text())
            event = report.get("event")
            if not event:
                continue
            latency = report["seconds_to_first_visible"]
            observed = datetime.fromisoformat(event["frame_ingested_at"].replace("Z", "+00:00")).timestamp() + latency
            if observed >= now - WINDOW:
                latencies.append(latency)
            if latest is None or observed > latest[0]:
                latest = (observed, {
                    "source": event["source_id"], "id": event["detection_id"],
                    "decoded": report["decoded_frames"], "inferred": report["selected_frames"],
                    "speed": report["speed"], "latency": latency, "at": observed,
                })
        except (OSError, ValueError, TypeError, KeyError):
            errors += 1
    jobs = []
    for path in (DATA / ".review").glob("review-*.json"):
        try:
            job = json.loads(path.read_text())
            if job["status"] in ("live", "error"):
                jobs.append({"source": path.stem, "at": path.stat().st_mtime,
                             "status": job["status"], "stage": job["stage"]})
        except (OSError, ValueError, KeyError):
            errors += 1
    history = []
    for path in sorted(DATA.glob("benchmarks/*/summary.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:12]:
        try:
            bench = json.loads(path.read_text())
            history.append({key: bench[key] for key in ("run", "provider", "streams", "start_utc",
                "measured_frames", "duration_s", "inference_fps", "target_fps", "quality_min_iou",
                "dropped_frames", "sql_latency_s")})
        except (OSError, ValueError, KeyError):
            errors += 1
    rows.sort(key=lambda row: (-row["at"], row["slot"]))
    return {
        "streams": rows[:64], "jobs": sorted(jobs, key=lambda r: -r["at"])[:8], "history": history, "last_review": latest[1] if latest else None,
        "latency": {"seconds": WINDOW, "count": len(latencies),
                    "p50": percentile(latencies, .5), "p95": percentile(latencies, .95)},
        "telemetry_errors": errors,
    }


def routine_load():
    with pymysql.connect(host=os.getenv("DB_HOST", "starrocks"),
                         port=int(os.getenv("DB_PORT", 9030)), user="root", database="visions",
                         connect_timeout=2, read_timeout=2, cursorclass=pymysql.cursors.DictCursor) as db:
        with db.cursor() as cur:
            cur.execute("SHOW ROUTINE LOAD FOR load_detections")
            job = cur.fetchone()
            if not job:
                return {"state": "Missing"}
            stats = json.loads(job["Statistic"])
            return {
                "state": job["State"], "loaded": stats["loadedRows"], "rejected": stats["errorRows"],
                "offsets": json.loads(job["Progress"]), "last_error": job["ReasonOfStateChanged"] or None,
                "tasks": int(job["CurrentTaskNum"]),
            }


def kafka_end():
    with closing(KafkaConsumer(bootstrap_servers=os.getenv("BROKER", "kafka:9092"),
                       group_id=None, enable_auto_commit=False,
                       api_version=(2, 8), request_timeout_ms=3000,
                       session_timeout_ms=2000, heartbeat_interval_ms=500, allow_auto_create_topics=False)) as consumer:
        # This project's topic is provisioned with one partition.
        part = TopicPartition(TOPIC, 0)
        return consumer.end_offsets([part])[part]


def storage_health():
    url = os.getenv("S3_ENDPOINT", "http://s3:9000") + "/minio/health/ready"
    with build_opener(ProxyHandler({})).open(url, timeout=2) as response:
        return response.status == 200


def host_resources(now):
    global _cpu
    cpu = list(map(int, Path("/proc/stat").read_text().splitlines()[0].split()[1:9]))
    total, idle = sum(cpu), cpu[3] + cpu[4]
    pct = None
    if _cpu and total > _cpu[0]:
        pct = 100 * (1 - (idle - _cpu[1]) / (total - _cpu[0]))
    _cpu = (total, idle)
    mem = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, rest = line.split(":", 1)
        if key in ("MemTotal", "MemAvailable"):
            mem[key] = 1024 * int(rest.split()[0])
    usage = shutil.disk_usage(DATA)
    gpu = None
    try:
        path = DATA / "monitor" / "gpu.csv"
        at = path.stat().st_mtime
        name, util, used, size = [piece.strip() for piece in path.read_text().strip().split(",")]
        if now - at <= STALE:
            gpu = {"name": name, "util": float(util), "used": float(used), "total": float(size), "at": at}
    except (OSError, ValueError):
        pass
    containers = {}
    try:
        states = json.loads((DATA / "monitor" / "containers.json").read_text())
        if now - states["at"] <= 8:
            containers = states["services"]
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return {"containers": containers, "cpu": pct, "ram_total": mem["MemTotal"], "ram_used": mem["MemTotal"] - mem["MemAvailable"],
            "disk_free": usage.free, "disk_total": usage.total, "gpu": gpu}


def capture():
    global _sample
    with _lock:
        now = time.time()
        if now - _sample.get("at", 0) < 1.5:
            return _sample
        probes = {key: _pool.submit(fn) for key, fn in (
            ("StarRocks", routine_load), ("Kafka", kafka_end), ("Object storage", storage_health))}
        result = workload(now)
        result["host"] = host_resources(now)
        result["services"] = {}
        for key, future in probes.items():
            try:
                value = future.result(timeout=4)
                if key == "StarRocks":
                    result["load"] = value
                elif key == "Kafka":
                    result["end_offset"] = value
                result["services"][key] = {"up": True}
            except Exception as ex:
                result["services"][key] = {"up": False, "error": type(ex).__name__}
        load = result.get("load", {})
        end = result.get("end_offset")
        consumed = str(load.get("offsets", {}).get("0", ""))
        result["lag"] = max(0, end - 1 - int(consumed)) if end is not None and consumed.isdigit() else None
        result["at"] = now
        _sample = result
        return result


@operations.get("/operations")
def dashboard():
    return send_from_directory(STATIC, "operations.html")


@operations.get("/api/operations")
def snapshot():
    response = jsonify(capture())
    response.headers["Cache-Control"] = "no-store"
    return response


@operations.get("/metrics")
def metrics():
    s = capture()
    lines = []
    seen = set()
    help_text = {
        "host_cpu_percent": "CPU busy percentage on the host since the preceding sample.",
        "host_memory_used_bytes": "Host memory in use, computed as total minus available.",
        "volume_available_bytes": "Available bytes on the private artifact filesystem.",
        "gpu_utilization_percent": "Device-wide utilization sampled by the NVIDIA driver.",
        "gpu_memory_used_mib": "Device-wide allocated GPU memory in MiB.",
        "routine_load_running": "One when StarRocks reports the Routine Load job running.",
        "routine_load_rejected_rows": "Total rejected rows in the current Routine Load job.",
        "routine_load_loaded_rows": "Total loaded records in the current Routine Load job.",
        "kafka_backlog_records": "Records between the Kafka log end and last applied offset.",
        "container_running": "One when the Docker Engine reports the service container running.",
        "container_healthy": "One when the configured Docker health check has passed.",
        "service_up": "One for a successful service protocol readiness probe.",
        "sql_visibility_seconds": "Nearest-rank wall-clock SQL visibility latency over five minutes.",
        "sql_visibility_samples": "Number of first SQL visibility observations in five minutes.",
        "stream_input_fps": "Configured source frames per second for an active worker.",
        "stream_decoded_fps": "Decoded frame completions per second in the trailing five seconds.",
        "stream_inference_fps": "Inference frame completions per second in the trailing five seconds.",
        "stream_detection_rate": "Detected faces per second in the trailing five seconds.",
        "stream_lag_ms": "Most recent delay relative to scheduled media playout in milliseconds.",
        "stream_frames": "Frames processed in the current warmup or measurement phase.",
        "stream_skipped": "Frames intentionally skipped in the current all-frame benchmark.",
        "stream_dropped": "Frames dropped in the current all-frame benchmark.",
        "stage_mean_ms": "Trailing five-second mean wall time per instrumented operation in milliseconds.",
    }

    def add(name, value, labels=""):
        if value is not None and math.isfinite(value):
            if name not in seen:
                lines.extend([f"# HELP visions_{name} {help_text[name]}", f"# TYPE visions_{name} gauge"])
                seen.add(name)
            lines.append(f"visions_{name}{labels} {float(value):.12g}")

    host = s["host"]
    load = s.get("load", {})
    gpu = host["gpu"] or {}
    add("host_cpu_percent", host["cpu"])
    add("host_memory_used_bytes", host["ram_used"])
    add("volume_available_bytes", host["disk_free"])
    add("gpu_utilization_percent", gpu.get("util"))
    add("gpu_memory_used_mib", gpu.get("used"))
    add("routine_load_running", load["state"] == "RUNNING" if load else None)
    add("routine_load_rejected_rows", load.get("rejected"))
    add("routine_load_loaded_rows", load.get("loaded"))
    add("kafka_backlog_records", s["lag"])
    for service, container in host["containers"].items():
        label = '{service="' + service + '"}'
        add("container_running", container["state"] == "running", label)
        if container["health"] != "none":
            add("container_healthy", container["health"] == "healthy", label)
    for service, probe in s["services"].items():
        add("service_up", probe["up"], '{service="' + service + '"}')
    for q in ("p50", "p95"):
        add("sql_visibility_seconds", s["latency"][q], '{stat="' + q + '"}')
    add("sql_visibility_samples", s["latency"]["count"])
    for stream in s["streams"]:
        if stream["active"]:
            labels = ('{slot="' + str(int(stream["slot"])) + '",provider="' + stream["provider"] +
                      '",run="' + stream["run"] + '"}')
            for field in ("input_fps", "decoded_fps", "inference_fps", "detection_rate", "lag_ms", "frames",
                          "skipped", "dropped"):
                add("stream_" + field, stream[field], labels)
            for stage, duration in stream["stages"].items():
                add("stage_mean_ms", duration, labels[:-1] + ',stage="' + stage.removesuffix("_ms") + '"}')
    return Response("\n".join(lines) + "\n", content_type="text/plain; version=0.0.4; charset=utf-8")
