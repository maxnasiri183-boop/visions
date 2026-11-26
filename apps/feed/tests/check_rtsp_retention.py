"""Exercise RTSP backlog and disk cleanup using disposable media and mock remote stores."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch



def check(root):
    os.environ.update(
        OPERATOR_DATA=str(root),
        FEED_RUN_ID="disk-test",
        FEED_MIN_FREE_BYTES="24",
        FEED_RECOVER_FREE_BYTES="30",
        FEED_RETENTION_MODE="disk",
    )
    from visions_feed import backend
    from visions_feed import retention
    from visions_feed import rtsp
    from visions_feed.db import connect, initialize

    initialize()
    db = connect()
    db.execute("INSERT INTO run_totals(run_id) VALUES(?)", (rtsp.RUN,))
    assert [retention.pressure(db, v) for v in (40, 23, 25, 30)] == [
        False,
        True,
        True,
        False,
    ]
    folder = root / "recordings" / "cam" / "rtsp-session"
    folder.mkdir(parents=True)
    sample = root / "input.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=s=96x96:r=5",
            "-t",
            "1",
            "-threads",
            "1",
            str(sample),
        ],
        check=True,
    )
    for i in range(4):
        segment = folder / f"{i:09d}.mp4"
        segment.write_bytes(sample.read_bytes())
        rtsp.register(db, "cam", folder.name, segment)
        rtsp.register(db, "cam", folder.name, segment)
    rows = db.execute("SELECT * FROM occurrences ORDER BY sequence").fetchall()
    assert [r["state"] for r in rows] == ["recorded"] * 3 + ["queued"]
    assert db.execute("SELECT sum(frames) FROM recording_skips").fetchone()[0] == 15
    import json

    (root / "pinned.json").write_text(json.dumps([rows[0]["occurrence_id"]]))
    s3 = MagicMock()
    s3.get_paginator.return_value.paginate.return_value = []

    class EndPass(BaseException):
        pass

    # One low-space pass; recover as soon as the first eligible segment is removed.
    def current_pressure(conn):
        return retention.pressure(
            conn, 22 if (root / rows[1]["input_path"]).exists() else 31
        )

    with (
        patch.object(backend, "store", return_value=s3),
        patch.object(backend, "sql", return_value=MagicMock()),
        patch.object(backend, "pressure", side_effect=current_pressure),
        patch.object(backend.time, "sleep", side_effect=EndPass),
    ):
        try:
            backend.expire()
        except EndPass:
            pass
    assert [bool((root / r["input_path"]).exists()) for r in rows] == [
        True,
        False,
        True,
        True,
    ]
    assert (
        db.execute("SELECT state FROM occurrences WHERE sequence=1").fetchone()[0]
        == "expired"
    )
    assert sample.is_file()
    print(
        "PASS: recorded all frames, skipped old inference, protected pins and queued work, stopped at high watermark"
    )


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        check(Path(tmp))
