"""Exercise persistent clip requests, resume, empty frames and canonical replay."""

import json
import os
import subprocess
import time
from uuid import uuid4

from acceptance import BUCKET, DOCKER, TOPIC, message_at, ready, row_after


def main():
    service = os.getenv("DETECTOR_SERVICE", "detector")
    source = "frame-check-" + uuid4().hex[:12]
    request = dict(source_id=source, clip="/input/feed.mkv", speed=4.0, scheduled_at=time.time() - 10)
    requests = [dict(request, request_id="first", start_index=9),
                dict(request, request_id="retry", start_index=9),
                dict(request, request_id="closed", start_index=20)]
    log = subprocess.check_output(
        DOCKER + ["run", "--rm", "--no-deps", "-T", service, "serve"],
        input="".join(json.dumps(row) + "\n" for row in requests), text=True,
        timeout=150)
    frames = [json.loads(line[12:]) for line in log.splitlines() if line.startswith("frame_event=")]
    done = [json.loads(line[10:]) for line in log.splitlines() if line.startswith("clip_done=")]
    assert done == [dict(request_id=r["request_id"], source_id=source, frame_count=20) for r in requests]
    stats = [json.loads(line[12:]) for line in log.splitlines() if line.startswith("frame_stats=")]
    assert len(stats) == 22 and all(row["lag_ms"] > 9000 for row in stats)
    assert log.count("worker_ready") == 1
    assert len(frames) == 22
    first, replay = frames[:11], frames[11:]
    db, s3 = ready()
    try:
        for index, (live, saved) in enumerate(zip(first, replay, strict=True), 9):
            assert live["source_id"] == saved["source_id"] == source
            assert live["frame_index"] == saved["frame_index"] == index
            assert live["pts"] == saved["pts"] == 100 * index
            assert live["time_base"] == saved["time_base"] == [1, 1000]
            assert live["frame_size"] == [512, 512] and live["video_track"] == 0
            assert live["schema_version"] == 1
            assert live["frame_ingested_at"] == saved["frame_ingested_at"] and live["frame_ingested_at"]
            assert live["manifest_key"] == saved["manifest_key"]
            manifest = json.loads(s3.get_object(Bucket=BUCKET, Key=live["manifest_key"])["Body"].read())
            assert manifest == [d["event"] for d in live["detections"]]
            if index >= 10:
                assert live["detections"] == saved["detections"] == []
                continue
            face, = live["detections"]
            repeated, = saved["detections"]
            event = face["event"]
            assert repeated["event"] == event and event["frame_ingested_at"] == live["frame_ingested_at"]
            assert repeated["topic"] == face["topic"] == TOPIC
            assert repeated["partition"] == face["partition"] and repeated["offset"] > face["offset"]
            key, payload = message_at(repeated["partition"], repeated["offset"])
            assert key == event["detection_id"] and json.loads(payload) == event
            assert row_after(db, key, repeated["partition"], repeated["offset"])["frame_index"] == 9
        with db.cursor() as cursor:
            cursor.execute("SELECT count(*) AS n FROM visions.face_detections WHERE source_id=%s", (source,))
            assert cursor.fetchone()["n"] == 1
    finally:
        db.close()
        s3.close()
    print(f"PASS {source} {service}: one session, three requests, 22 frames, stable IDs, one SQL row")


if __name__ == "__main__":
    main()
