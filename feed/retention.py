"""Hysteresis for the operator recording volume, persisted across restarts."""

import os
import shutil
import time

from db import DATA


def pressure(db, free=None):
    low = int(os.getenv("FEED_MIN_FREE_BYTES", str(24 * 1024**3)))
    high = int(os.getenv("FEED_RECOVER_FREE_BYTES", str(30 * 1024**3)))
    if not 0 < low < high:
        raise ValueError("Disk recovery threshold must exceed the free-space reserve")
    free = shutil.disk_usage(DATA).free if free is None else free
    previous = db.execute("SELECT active FROM retention_pressure WHERE id=1").fetchone()
    active = free < (high if previous and previous[0] else low)
    if not previous or bool(previous[0]) != active:
        db.execute(
            "INSERT OR REPLACE INTO retention_pressure VALUES(1,?,?)",
            (int(active), time.time()),
        )
    return active
