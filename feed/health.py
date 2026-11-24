import sqlite3
import sys
import time
from db import DB

db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=2)
r = db.execute(
    "SELECT heartbeat_at,state FROM service_state WHERE service=?", (sys.argv[1],)
).fetchone()
sys.exit(
    0
    if r
    and time.time() - r[0] < 45
    and r[1] not in ("source_outage", "storage_guard", "waiting_backend")
    else 1
)
