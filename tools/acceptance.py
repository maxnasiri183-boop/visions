"""Run from the repo root after starting Compose. Evidence stays under private/."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import boto3
import pymysql
from botocore.config import Config
from botocore.exceptions import ClientError
from kafka import KafkaConsumer, TopicPartition
from kafka.admin import KafkaAdminClient, NewTopic
from kafka.errors import TopicAlreadyExistsError
from PIL import Image

TOPIC = "face.detected.v1"
BUCKET = "visions-private"
BROKER = os.getenv("BROKER", "127.0.0.1:19092")
PRIVATE = Path("private")
DOCKER = ["docker", "compose"]


def storage():
    password = os.getenv("S3_PASSWORD") or next(
        line.split("=", 1)[1]
        for line in Path(".env").read_text().splitlines()
        if line.startswith("S3_PASSWORD=")
    )
    return boto3.client(
        "s3",
        endpoint_url=os.getenv("S3_ENDPOINT", "http://127.0.0.1:19000"),
        region_name="us-east-1",
        aws_access_key_id="visions-local",
        aws_secret_access_key=password,
        config=Config(proxies={}, s3={"addressing_style": "path"}),
    )


def provision():
    s3 = storage()
    try:
        s3.create_bucket(Bucket=BUCKET)
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "BucketAlreadyOwnedByYou":
            raise
    admin = KafkaAdminClient(bootstrap_servers=BROKER)
    try:
        admin.create_topics([NewTopic(TOPIC, 1, 1)])
    except TopicAlreadyExistsError:
        pass
    finally:
        admin.close()
    db = pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "19030")),
        user="root",
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )
    with db.cursor() as cur:
        cur.execute("SHOW BACKENDS")
        if not any(str(r["Alive"]).lower() == "true" for r in cur.fetchall()):
            raise RuntimeError("StarRocks BE not ready")
        for statement in (
            Path("deploy/detections.sql").read_text().strip().rstrip(";").split(";")
        ):
            if statement.strip().startswith("CREATE ROUTINE LOAD"):
                cur.execute("SHOW ROUTINE LOAD")
                if any(job["Name"] == "load_detections" for job in cur.fetchall()):
                    continue
            cur.execute(statement)
    return db, s3


def ready():
    end = time.monotonic() + 180
    while True:
        try:
            return provision()
        except pymysql.ProgrammingError as exc:
            # A newly registered BE can report Alive before its first disk heartbeat.
            if "Current available backends: []" not in str(exc) or time.monotonic() >= end:
                raise
            print("Waiting for StarRocks backend capacity", flush=True)
            time.sleep(3)
        except Exception as exc:
            if time.monotonic() >= end:
                raise
            print("Waiting for local services:", type(exc).__name__, flush=True)
            time.sleep(3)


def run_worker(source, speed):
    output = subprocess.check_output(
        DOCKER
        + [
            "run",
            "--rm",
            "--no-deps",
            "-T",
            os.getenv("DETECTOR_SERVICE", "detector"),
            "/input/feed.mkv",
            source,
            "5",
            str(speed),
        ],
        text=True,
    )
    print(output, flush=True)
    matches = re.findall(
        r"detection_id=([\w-]+) topic=face.detected.v1 partition=(\d+) offset=(\d+)",
        output,
    )
    assert len(matches) == 1 and "decoded_frames=20 selected_frame=5" in output, output
    identity, part, offset = matches[0]
    return identity, int(part), int(offset)


def message_at(part, offset):
    tp = TopicPartition(TOPIC, part)
    consumer = KafkaConsumer(
        bootstrap_servers=BROKER, enable_auto_commit=False, group_id=None
    )
    try:
        consumer.assign([tp])
        consumer.seek(tp, offset)
        records = consumer.poll(timeout_ms=10000, max_records=1).get(tp, [])
        assert records and records[0].offset == offset
        return records[0].key.decode(), records[0].value
    finally:
        consumer.close()


def row_after(db, identity, part, offset):
    end = time.monotonic() + 120
    with db.cursor() as cur:
        while time.monotonic() < end:
            cur.execute("SHOW ROUTINE LOAD FOR load_detections")
            job = cur.fetchone()
            if job["State"] in ("PAUSED", "CANCELLED", "STOPPED"):
                raise RuntimeError(str(job))
            progress = json.loads(job["Progress"]).get(str(part), "-1")
            if str(progress).isdigit() and int(progress) >= offset:
                cur.execute(
                    "SELECT * FROM face_detections WHERE detection_id=%s", (identity,)
                )
                rows = cur.fetchall()
                if len(rows) == 1:
                    return rows[0]
            time.sleep(0.1)
    raise TimeoutError(f"Routine Load did not make offset {offset} visible: {job}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--speed", default=1.0, type=float)
    args = parser.parse_args()
    db, s3 = ready()
    source = "review-" + uuid4().hex[:12]
    folder = PRIVATE / source
    folder.mkdir()
    # Observe SQL while the first worker is still playing to avoid timing container teardown.
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(run_worker, source, args.speed)
        deadline = time.monotonic() + 180
        with db.cursor() as cur:
            while time.monotonic() < deadline:
                cur.execute(
                    "SELECT detection_id, frame_ingested_at FROM face_detections WHERE source_id=%s",
                    (source,),
                )
                visible = cur.fetchone()
                if visible:
                    observed = datetime.now(timezone.utc)
                    break
                if first.done():
                    first.result()
                time.sleep(0.1)
            else:
                raise TimeoutError("First SQL row not visible")
        identity, part, before = first.result()
    key, payload = message_at(part, before)
    event = json.loads(payload)
    assert key == identity == visible["detection_id"] == event["detection_id"]
    assert event["source_id"] == source and event["frame_index"] == 5
    clip = PRIVATE / "feed.mkv"
    assert hashlib.sha256(clip.read_bytes()).hexdigest() == event["asset_sha256"]
    probe = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=time_base:frame=best_effort_timestamp",
            "-of",
            "json",
            str(clip),
        ]
    )
    meta = json.loads(probe)
    assert event["pts"] == meta["frames"][5]["best_effort_timestamp"]
    assert event["time_base"] == list(
        map(int, meta["streams"][0]["time_base"].split("/"))
    )
    row = row_after(db, identity, part, before)
    for field, value in event.items():
        actual = json.loads(row[field]) if isinstance(value, list) else row[field]
        assert actual == value, (field, actual, value)
    assert event["crop_uri"] == f"s3://{BUCKET}/crops/{identity}.png"
    crop = s3.get_object(Bucket=BUCKET, Key=f"crops/{identity}.png")["Body"].read()
    assert hashlib.sha256(crop).hexdigest() == event["crop_sha256"]
    frame = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(clip),
            "-vf",
            "select=eq(n\\,5)",
            "-frames:v",
            "1",
            "-threads",
            "1",
            "-c:v",
            "png",
            "-f",
            "image2pipe",
            "-",
        ]
    )
    image = Image.open(BytesIO(frame)).convert("RGB")
    x, y, w, h = event["bbox_xywh"]
    # Manual face rectangle (left, top, right, bottom) on the pinned 512px fixture.
    left, top, right, bottom = 176, 66, 277, 182
    intersection = max(0, min(x + w, right) - max(x, left)) * max(
        0, min(y + h, bottom) - max(y, top)
    )
    iou = intersection / (w * h + (right - left) * (bottom - top) - intersection)
    assert iou >= 0.5, (event["bbox_xywh"], iou)
    actual = Image.open(BytesIO(crop)).convert("RGB")
    assert (
        actual.size == (w, h)
        and actual.tobytes() == image.crop((x, y, x + w, y + h)).tobytes()
    )
    second_id, second_part, after = run_worker(source, args.speed)
    assert (second_id, second_part) == (identity, part) and after > before
    assert message_at(part, after) == (key, payload)
    assert row_after(db, identity, part, after) == row
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM face_detections WHERE source_id=%s", (source,)
        )
        assert cur.fetchone()["n"] == 1
    latency = (
        observed
        - datetime.fromisoformat(event["frame_ingested_at"].replace("Z", "+00:00"))
    ).total_seconds()
    report = {
        "event": event,
        "topic": TOPIC,
        "partition": part,
        "offsets": [before, after],
        "sql": row,
        "logical_rows_after_replay": 1,
        "decoded_frames": 20,
        "selected_frames": 1,
        "bbox_iou": iou,
        "speed": args.speed,
        "seconds_to_first_visible": latency,
        "crop_pixels_equal": True,
    }
    (folder / "frame.png").write_bytes(frame)
    (folder / "crop.png").write_bytes(crop)
    (folder / "report.json").write_text(json.dumps(report, indent=2))
    print(f"PASS {folder}/report.json | {latency:.3f}s first SQL visibility")
    db.close()


if __name__ == "__main__":
    main()
