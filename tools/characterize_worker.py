"""Check a worker image against the deployed CPU/CUDA fixture contracts.

Start the data services and create the private fixture as for acceptance.py.
COMPOSE_FILE and DETECTOR_SERVICE select a particular worker service.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from acceptance import BUCKET, DOCKER, ready


def main():
    service = os.getenv("DETECTOR_SERVICE", "detector")
    output = subprocess.check_output([sys.executable, "tools/acceptance.py", "--speed", "1"], text=True)
    result = re.search(r"^PASS (.+/report.json) \|", output, re.MULTILINE)
    assert result, output
    report = json.loads(Path(result[1]).read_text())
    event = report["event"]
    assert event["schema_version"] == 1 and event["video_track"] == 0
    assert event["bbox_xywh"] == [178, 63, 92, 112] and event["pts"] == 500
    assert event["frame_size"] == [512, 512] and event["time_base"] == [1, 1000]
    assert abs(event["confidence"] - 0.94344) < 0.0001
    expected = [(201.49, 105.04), (245.21, 104.91), (220.70, 127.14),
                (203.91, 145.27), (242.34, 145.06)]
    for actual, point in zip(event["landmarks_xy"], expected, strict=True):
        assert all(abs(a-b) < 0.1 for a, b in zip(actual, point, strict=True))
    assert event["crop_sha256"] == "071e506d2ac326e9a3086bab784b76c9ac9ecb47046757420425465384af0827"
    if service.endswith("gpu"):
        assert re.search(r"ort_profile_cuda_kernels=[1-9]\d* ort_profile_cpu_kernels=0", output)
    source = "blank-" + uuid4().hex[:12]
    db, s3 = ready()
    try:
        key = f"frames/{source}/{event['asset_sha256']}/{event['detector_version']}/v0/15.json"
        for rerun in (False, True):
            blank = subprocess.check_output(
                DOCKER + ["run", "--rm", "--no-deps", "-T", service,
                          "/input/feed.mkv", source, "15", "2"], text=True)
            assert "detection_id=" not in blank and "decoded_frames=20 selected_frame=15" in blank
            stats, = [json.loads(line.removeprefix("frame_stats=")) for line in blank.splitlines()
                      if line.startswith("frame_stats=")]
            assert stats["detections"] == 0 and stats["ran_inference"] == (not rerun)
            assert stats["media_pts_s"] == 1.5 and stats["kafka_publish_ms"] == 0
            assert json.loads(s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()) == []
        with db.cursor() as cursor:
            cursor.execute("SELECT count(*) AS n FROM visions.face_detections WHERE source_id=%s", (source,))
            assert cursor.fetchone()["n"] == 0
    finally:
        db.close()
        s3.close()
    print(f"PASS {result[1]} {service} face, blank and replay contracts")


if __name__ == "__main__":
    main()
