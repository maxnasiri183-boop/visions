"""Benchmark the pinned 10 FPS fixture through S3, Kafka and StarRocks."""

import argparse
import hashlib
import json
import math
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from uuid import uuid4

from acceptance import ready

FPS = 10
FRAMES = 20
SHA256 = "51238b372406713fbe4ab379b60afbd084e158deb88942b64c097d82dbfb9e9e"


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


def write_record(file, row):
    file.write(json.dumps(row, separators=(",", ":")) + "\n")
    file.flush()


def intersection_over_union(box):
    x, y, w, h = box
    intersection = max(0, min(x + w, 277) - max(x, 176)) * max(0, min(y + h, 182) - max(y, 66))
    return intersection / (w * h + 101 * 116 - intersection)


def worker(compose, service, folder, stream, source, loops, provider):
    cmd = compose + [
        "run", "--rm", "--no-deps", "-T",
        "-e", f"BENCH_START_FILE=/input/benchmarks/{folder.name}/start",
        "-e", "JAVA_TOOL_OPTIONS=-Xmx512m -XX:ActiveProcessorCount=2",
        service, "/input/feed.mkv", source, "all", "1", str(loops),
    ]
    measured = []
    requested = None
    profile = {}
    with (folder / f"stream-{stream}.jsonl").open("w") as frame_file, (
        folder / f"worker-{stream}.log"
    ).open("w") as log, subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    ) as process:
        for line in process.stdout:
            log.write(line)
            if line.startswith("onnxruntime_provider_requested="):
                requested = line.strip().split("=", 1)[1]
            if line.startswith("ort_profile_cuda_kernels="):
                profile = dict(item.split("=") for item in line.split())
            if line.rstrip() == "warmup_complete":
                (folder / f"ready-{stream}").touch()
            if line.startswith("frame_stats="):
                frame = json.loads(line.split("=", 1)[1])
                frame.update(stream=stream, provider=provider, input_fps=FPS)
                if profile:
                    frame["cuda_kernels"] = int(profile["ort_profile_cuda_kernels"])
                    frame["cpu_kernels"] = int(profile["ort_profile_cpu_kernels"])
                write_record(frame_file, frame)
                if not frame["warmup"]:
                    measured.append(frame)
        if process.wait():
            raise RuntimeError(f"Detector exited {process.returncode}: {log.name}")
    if requested != provider or (provider == "cuda" and int(profile.get("ort_profile_cuda_kernels", 0)) == 0):
        raise RuntimeError(f"Provider verification failed: requested={requested}, profile={profile}")
    if len(measured) != loops * FRAMES or not all(f["ran_inference"] for f in measured):
        raise RuntimeError(f"Incomplete inference on {source}: {len(measured)} measured frames")
    return measured


def read_gpu():
    if not shutil.which("nvidia-smi"):
        return None
    try:
        result = subprocess.run(
            ["nvidia-smi", "--id=0", "--query-gpu=utilization.gpu,memory.used",
             "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3, check=True,
        )
        usage, memory = result.stdout.strip().split(",")
        return {"util_pct": int(usage), "vram_mib": int(memory), "time": time.time()}
    except (subprocess.SubprocessError, ValueError):
        return None


def measure(db, compose, service, streams, loops, provider):
    run = "bench-" + uuid4().hex[:12]
    folder = Path("private/benchmarks") / run
    folder.mkdir(parents=True)
    config = json.loads(subprocess.check_output(compose + ["--profile", "*", "config", "--format", "json"]))
    image = config["services"][service].get("image", f"{config['name']}-{service}")
    image_id = subprocess.check_output(["docker", "image", "inspect", image, "-f", "{{.Id}}"], text=True).strip()
    sources = [f"{run}-{s}" for s in range(streams)]
    observed, latencies, gpu, ious = {}, [], [], []
    with ThreadPoolExecutor(max_workers=streams) as pool, (
        folder / "sql.jsonl"
    ).open("w") as sql_samples, (folder / "gpu.jsonl").open("w") as gpu_samples:
        jobs = [pool.submit(worker, compose, service, folder, i, source, loops, provider)
                for i, source in enumerate(sources)]
        deadline = time.monotonic() + 175
        while len(list(folder.glob("ready-*"))) < streams:
            for job in jobs:
                if job.done():
                    job.result()
            if time.monotonic() > deadline:
                raise TimeoutError("Workers failed to warm up")
            time.sleep(.05)
        start = time.time()
        (folder / "start").touch()
        last_gpu = 0
        drained_at = None
        with db.cursor() as cur:
            while True:
                cur.execute(
                    "SELECT detection_id, source_id, frame_index, bbox_xywh, frame_ingested_at "
                    "FROM visions.face_detections WHERE source_id IN %s", (tuple(sources),),
                )
                rows = cur.fetchall()
                now = time.time()
                for row in rows:
                    identity = row["detection_id"]
                    if identity in observed:
                        continue
                    stream = sources.index(row["source_id"])
                    index = row["frame_index"]
                    observed[identity] = (stream, index)
                    ious.append(intersection_over_union(json.loads(row["bbox_xywh"])))
                    ingested = datetime.fromisoformat(row["frame_ingested_at"]).timestamp()
                    latencies.append(now - ingested)
                    write_record(sql_samples, {"stream": stream, "index": index, "wall_time": now,
                                              "latency_s": now - ingested})
                if now - last_gpu >= 1:
                    sample = read_gpu()
                    if sample:
                        gpu.append(sample)
                        write_record(gpu_samples, sample)
                    last_gpu = now
                for job in jobs:
                    if job.done():
                        job.result()
                if all(job.done() for job in jobs):
                    frames = [f for job in jobs for f in job.result()]
                    if len(observed) == sum(f["detections"] for f in frames):
                        break
                    if drained_at is None:
                        drained_at = time.monotonic()
                    if time.monotonic() - drained_at > 120:
                        raise TimeoutError(f"SQL visibility timeout: {len(observed)} rows in {folder}")
                time.sleep(.15)
    duration = max(f["wall_time"] for f in frames) - start
    for f in frames:
        if f["detections"] != int(f["index"] % FRAMES < FRAMES // 2):
            raise ValueError(f"Detection count differs from label in {folder} at frame {f['index']}")
    expected = {(s, i) for s in range(streams) for i in range(loops * FRAMES) if i % FRAMES < FRAMES // 2}
    if set(observed.values()) != expected or len(observed) != len(expected) or min(ious) < .5:
        raise ValueError(f"Unexpected SQL observations or bounding box for {folder}")
    active_gpu = [s for s in gpu if s["time"] <= start + duration]
    summary = dict(
        run=run, provider=provider, image_id=image_id, start_utc=datetime.fromtimestamp(start, timezone.utc).isoformat(),
        streams=streams, width=512, height=512, source_fps=FPS, sampling="all",
        warmup_frames_per_stream=FRAMES, batch_size=1, measured_frames=len(frames),
        duration_s=duration, target_fps=streams * FPS, inference_fps=len(frames) / duration,
        per_stream_fps=[len(j.result())/(j.result()[-1]["wall_time"]-start) for j in jobs],
        dropped_frames=0, detections=len(observed), quality_min_iou=min(ious),
        sql_latency_s={k: percentile(latencies, q) for k, q in [("p50", .5), ("p95", .95)]},
        stage_p95_ms={}, gpu_samples=len(active_gpu),
        mean_gpu_pct=mean(s["util_pct"] for s in active_gpu) if active_gpu else None,
        peak_vram_mib=max((s["vram_mib"] for s in active_gpu), default=None),
    )
    for key in ("decode_ms", "preprocess_ms", "inference_ms", "crop_upload_ms", "manifest_io_ms", "kafka_publish_ms", "lag_ms"):
        summary["stage_p95_ms"][key] = percentile([
            f[key] for f in frames if f["detections"] or key not in ("crop_upload_ms", "kafka_publish_ms")
        ], .95)
    if provider == "cuda":
        summary["profiled_nodes_per_stream"] = [
            {key: job.result()[0][key] for key in ("cuda_kernels", "cpu_kernels")} for job in jobs
        ]
    (folder / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--streams", type=int, nargs="+", default=[1, 2, 4, 8], choices=(1, 2, 4, 8))
    parser.add_argument("--loops", type=int, default=15, help="Measured plays of the two-second clip per stream")
    parser.add_argument("--provider", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("-f", "--compose-file", action="append", help="Compose files in merge order")
    args = parser.parse_args()
    if args.loops < 1 or hashlib.sha256(Path("private/feed.mkv").read_bytes()).hexdigest() != SHA256:
        parser.error("Positive loop count and the pinned feed checksum are required")
    db, s3 = ready()
    s3.close()
    files = args.compose_file or (["compose.yaml", "compose.gpu.yaml"] if args.provider == "cuda" else ["compose.yaml"])
    compose = ["docker", "compose"] + [arg for filename in files for arg in ("-f", filename)]
    try:
        for streams in args.streams:
            measure(db, compose, "detector-gpu" if args.provider == "cuda" else "detector",
                    streams, args.loops, args.provider)
    finally:
        db.close()


if __name__ == "__main__":
    main()
