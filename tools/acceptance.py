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

from PIL import Image

from visions_review.evidence import BUCKET, TOPIC, message_at, ready, row_after

PRIVATE = Path("private")
DOCKER = ["docker", "compose"]


def parse_ingest_time(value: str) -> datetime:
    # Java Instant can emit nine fractional digits. Python 3.10 accepts microseconds.
    microseconds = re.sub(r"(\.\d{6})\d{3}Z$", r"\1Z", value)
    return datetime.fromisoformat(microseconds.replace("Z", "+00:00"))


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
        - parse_ingest_time(event["frame_ingested_at"])
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
