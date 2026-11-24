"""Local review API. One Waitress process owns the bounded file-feed job queue."""
import hashlib
import json
import logging
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from flask import Flask, abort, jsonify, request, send_file
from PIL import Image
from waitress import serve

import acceptance as trace

app = Flask(__name__, static_folder="static")
app.config.update(MAX_CONTENT_LENGTH=2048, TRUSTED_HOSTS=["localhost", "127.0.0.1", "review", "[::1]"])
private = Path("private").resolve()
state = private / ".review"
media = state / "media"
clip = private / "feed.mkv"
lock = threading.Lock()
source_pattern = re.compile(r"review-[a-f0-9]{12}")
worker = "/opt/worker/bin/detection-worker"
clip_hash = "51238b372406713fbe4ab379b60afbd084e158deb88942b64c097d82dbfb9e9e"


def read(path):
    return json.loads(path.read_text()) if path.is_file() else {}


def write(path, value):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value))
    tmp.replace(path)


def initialize():
    if hashlib.sha256(clip.read_bytes()).hexdigest() != clip_hash:
        raise ValueError("Fixed clip checksum mismatch. Run tools/fixture.py.")
    media.mkdir(exist_ok=True, parents=True)
    if not (media / "ready").exists():
        args = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(clip)]
        subprocess.run(args + ["-threads", "1", "-start_number", "0", str(media / "%d.png")], check=True)
        subprocess.run(args + ["-c:v", "libvpx-vp9", "-pix_fmt", "yuv420p", "-g", "1",
                               "-threads", "2", str(media / "feed.webm")], check=True)
        (media / "ready").touch()
    for path in state.glob("review-*.json"):
        status = read(path)
        if status["status"] == "live":
            status.update(status="error", stage="Worker interrupted by service restart")
            write(path, status)


def status_path(source):
    return state / (source + ".json")


def details(source):
    if not source_pattern.fullmatch(source):
        abort(404)
    report = read(private / source / "report.json")
    job = read(status_path(source))
    if not (report or job):
        abort(404)
    return {"source": source, "status": "saved", "frame": 5, "report": report, **job}


def run_worker(source, frame, speed):
    with (state / (source + ".log")).open("a") as log:
        result = subprocess.run([worker, str(clip), source, str(frame), str(speed)],
                                capture_output=True, text=True, timeout=120)
        log.write(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError("Detector exited with code " + str(result.returncode))
    match = re.search(r"detection_id=([\w-]+) topic=face.detected.v1 partition=(\d+) offset=(\d+)", result.stdout)
    if not re.search(rf"decoded_frames=20 selected_frame={frame}\b", result.stdout):
        raise RuntimeError("Incomplete decode")
    return (match[1], int(match[2]), int(match[3])) if match else None


def execute(source, frame, speed, replay):
    from concurrent.futures import ThreadPoolExecutor
    path = status_path(source)
    job = read(path)

    def stage(message):
        job.update(stage=message)
        write(path, job)

    db = None
    try:
        stage("Checking storage, Kafka and SQL")
        db, s3 = trace.ready()
        stage("Replaying the observation" if replay else "Decoding clip and running YuNet")
        observed = None
        with ThreadPoolExecutor(max_workers=1) as pool:
            task = pool.submit(run_worker, source, frame, speed)
            if not replay and frame < 10:
                end = time.monotonic() + 150
                with db.cursor() as cur:
                    while time.monotonic() < end:
                        cur.execute("SELECT detection_id FROM face_detections WHERE source_id=%s", (source,))
                        if cur.fetchone():
                            observed = datetime.now(timezone.utc)
                            break
                        if task.done():
                            if not task.result():
                                raise RuntimeError("Expected a detection on the labeled frame")
                            if job["stage"] != "Waiting for SQL visibility":
                                stage("Waiting for SQL visibility")
                        time.sleep(.1)
            ack = task.result()
        folder = private / source
        report = read(folder / "report.json")
        if ack:
            identity, part, offset = ack
            stage("Verifying event, crop and SQL row")
            key, payload = trace.message_at(part, offset)
            event = json.loads(payload)
            if not (key == identity == event["detection_id"] and event["source_id"] == source
                    and event["frame_index"] == frame and event["asset_sha256"] == clip_hash):
                raise RuntimeError("Kafka observation mismatch")
            row = trace.row_after(db, identity, part, offset)
            for field, expected in event.items():
                actual = json.loads(row[field]) if isinstance(expected, list) else row[field]
                if actual != expected:
                    raise ValueError("SQL mismatch for " + field)
            if replay:
                if event != report["event"] or trace.message_at(part, report["offsets"][0]) != (key, payload):
                    raise ValueError("Replay payload changed")
                with db.cursor() as cur:
                    cur.execute("SELECT COUNT(*) AS n FROM face_detections WHERE detection_id=%s", (identity,))
                    count = cur.fetchone()["n"]
                offsets = job.get("replay_offsets", []) + [offset]
                if count != 1 or offset <= max(report["offsets"] + offsets[:-1]):
                    raise ValueError("Replay offset or row count mismatch")
                job.update(replay_offsets=offsets, replay_rows=count)
            else:
                crop = s3.get_object(Bucket=trace.BUCKET, Key=f"crops/{identity}.png")["Body"].read()
                x,y,w,h = event["bbox_xywh"]
                original = Image.open(media / f"{frame}.png").convert("RGB").crop((x,y,x+w,y+h))
                actual = Image.open(BytesIO(crop)).convert("RGB")
                if (hashlib.sha256(crop).hexdigest() != event["crop_sha256"] or actual.size != original.size
                        or actual.tobytes() != original.tobytes()):
                    raise ValueError("Stored crop verification failed")
                if observed is None:
                    raise TimeoutError("First SQL visibility was not observed")
                latency = (observed - datetime.fromisoformat(event["frame_ingested_at"])).total_seconds()
                report = dict(event=event, sql=row, topic=trace.TOPIC, partition=part,
                              offsets=[offset], seconds_to_first_visible=latency, crop_pixels_equal=True)
        else:
            manifest = f"frames/{source}/{clip_hash}/yunet-2023mar-ort640-bgr-v1/v0/{frame}.json"
            if replay or frame < 10 or json.loads(s3.get_object(Bucket=trace.BUCKET, Key=manifest)["Body"].read()) != []:
                raise ValueError("Expected a verified empty frame manifest")
            report = dict(event=None, offsets=[], sql=None)
        if not replay:
            folder.mkdir(exist_ok=True)
            report.update(decoded_frames=20, selected_frames=1, speed=speed, selected_frame=frame)
            write(folder / "report.json", report)
        job.update(status="saved", stage="Completed")
    except Exception as exc:
        logging.exception("Job failed for %s", source)
        job.update(status="error", stage=f"{type(exc).__name__}: {str(exc)[:240]}")
    finally:
        try:
            write(path, job)
            if db:
                db.close()
        finally:
            lock.release()


@app.get("/")
def home():
    return app.send_static_file("index.html")


@app.get("/health")
def health():
    return jsonify(ready=(media / "ready").is_file())


@app.get("/media/<name>")
def get_media(name):
    if name != "feed.webm" and not re.fullmatch(r"(?:[0-9]|1[0-9])\.png", name):
        abort(404)
    return send_file(media / name, conditional=True)


@app.get("/api/runs")
def runs():
    sources = {p.parent.name for p in private.glob("review-*/report.json")}
    sources.update(p.stem for p in state.glob("review-*.json"))
    records = [details(s) for s in sources if source_pattern.fullmatch(s)]
    return jsonify(sorted(records, key=lambda r: r.get("started", (r["report"].get("event") or {}).get("frame_ingested_at","")), reverse=True))


@app.get("/api/runs/<source>/crop")
def crop(source):
    event = details(source)["report"].get("event")
    if not event:
        abort(404)
    data = trace.storage().get_object(Bucket=trace.BUCKET, Key=f"crops/{event['detection_id']}.png")["Body"].read()
    if hashlib.sha256(data).hexdigest() != event["crop_sha256"]:
        abort(502)
    return send_file(BytesIO(data), mimetype="image/png")


@app.post("/api/jobs")
def jobs():
    if not request.is_json or request.headers.get("X-Review-Action") != "job":
        abort(403)
    body = request.get_json()
    if not isinstance(body, dict):
        abort(400)
    replay = body.get("action") == "replay"
    if replay:
        source = body.get("source", "")
        old = details(source)
        if not old["report"].get("event"):
            abort(409)
        frame, speed = old["report"]["event"]["frame_index"], old["report"]["speed"]
    else:
        if body.get("action") != "start":
            abort(400)
        frame, speed = body.get("frame"), body.get("speed")
        if type(frame) is not int or not 0 <= frame < 20 or speed not in (.5,1,2):
            abort(400)
        source = "review-" + uuid4().hex[:12]
    if not lock.acquire(blocking=False):
        return jsonify(error="A worker job is already running"), 409
    try:
        job = read(status_path(source))
        job.update(status="live", frame=frame, speed=speed, stage="Queued", action=body["action"])
        job.setdefault("started", datetime.now(timezone.utc).isoformat())
        write(status_path(source), job)
        threading.Thread(target=execute, args=(source,frame,speed,replay), daemon=True).start()
    except Exception:
        lock.release()
        raise
    return jsonify(source=source), 202


if __name__ == "__main__":
    initialize()
    try:
        from operations import operations
    except ModuleNotFoundError as exc:
        if exc.name != "operations":
            raise
    else:
        app.register_blueprint(operations)
    serve(app, host="0.0.0.0", port=8091, threads=8)
