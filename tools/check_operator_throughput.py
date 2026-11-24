"""Measure the isolated replay-lab feed against Kafka and StarRocks from the exporter container."""

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from kafka import KafkaConsumer, KafkaProducer, TopicPartition

sys.path.insert(0, "/feed")
from backend import TOPIC, sql

SOURCE = "replay-lab"
RUN = os.environ["FEED_RUN_ID"]
DATA = Path(os.getenv("OPERATOR_DATA", "/data"))


def api(path, body=None):
    req = Request(
        path,
        data=json.dumps(body).encode() if body else None,
        headers={"Content-Type": "application/json", "X-Visions-Action": "operator"},
    )
    with urlopen(req, timeout=15) as result:
        return json.load(result)


def wait_for(get, timeout=90):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        value = get()
        if value:
            return value
        time.sleep(0.3)
    raise TimeoutError(get.__doc__ or "Timed out waiting for pipeline")


def snapshot(db):
    # Current retained rows and expiry summaries together reconstruct the lifetime SQL count.
    db.execute("BEGIN")
    try:
        r = db.execute(
            "SELECT published,sql_visible FROM throughput_source WHERE run_id=? AND source_id=?",
            (RUN, SOURCE),
        ).fetchone()
        cur = db.execute(
            "SELECT next_offset,missed_offsets FROM throughput_cursor WHERE partition_id=0"
        ).fetchone()
        retained = db.execute(
            """SELECT count(*) FROM observations o JOIN occurrences u USING(occurrence_id)
            WHERE u.run_id=? AND u.source_id=? AND o.sql_visible_at IS NOT NULL""",
            (RUN, SOURCE),
        ).fetchone()[0]
        expired = db.execute(
            """SELECT coalesce(sum(detections-missing_sql),0) FROM expiry_counts JOIN occurrences
            USING(occurrence_id) WHERE run_id=? AND source_id=?""",
            (RUN, SOURCE),
        ).fetchone()[0]
        assert r and cur and r["sql_visible"] == retained + expired, (
            "Distinct SQL counter differs from durable observation evidence"
        )
        return dict(
            at=time.time(),
            published=r["published"],
            sql_visible=r["sql_visible"],
            next_offset=cur["next_offset"],
            missed_offsets=cur["missed_offsets"],
        )
    finally:
        db.rollback()


def kafka_span(consumer, db, begin, end):
    tp = TopicPartition(TOPIC, 0)
    ids = {
        r[0]
        for r in db.execute(
            "SELECT occurrence_id FROM occurrences WHERE run_id=? AND source_id=?",
            (RUN, SOURCE),
        )
    }
    data = []
    consumer.seek(tp, begin)
    timeout = time.monotonic() + 30
    while consumer.position(tp) < end:
        if time.monotonic() > timeout:
            raise TimeoutError("Kafka evidence scan did not reach its final offset")
        for record in consumer.poll(timeout_ms=300, max_records=500).get(tp, []):
            if record.offset >= end:
                break
            event = json.loads(record.value)
            if event["source_id"] in ids:
                data.append((record.offset, event["detection_id"]))
    return data


def reconcile_span(db, consumer, begin, end):
    records = kafka_span(consumer, db, begin["next_offset"], end["next_offset"])
    assert (
        end["missed_offsets"] == begin["missed_offsets"]
        and len(records) == end["published"] - begin["published"]
    )
    return records


def prometheus(expression, end):
    url = "http://prometheus:9090/api/v1/query?" + urlencode(
        {"query": expression, "time": end}
    )
    payload = api(url)
    assert payload["status"] == "success"
    return payload["data"]["result"]


def scraped_evidence(db, consumer, begin, end):
    """Match actual common Prometheus scrapes by their durable processing watermarks."""
    duration = int(end["at"] - begin["at"])
    source = '{source="' + SOURCE + '"}'
    metrics = {
        "published": "published_observations_total" + source,
        "sql_visible": "sql_visible_observations_total" + source,
        "next_offset": 'publication_next_offset{partition="0"}',
        "watermark": "last_sql_visible_at" + source,
    }
    by_metric = {}
    for key, selector in metrics.items():
        series = prometheus(
            "visions_operator_" + selector + "[" + str(duration) + "s]", end["at"]
        )
        assert len(series) == 1, "Missing Prometheus " + key
        by_metric[key] = {float(t): float(val) for t, val in series[0]["values"]}
    common = sorted(set.intersection(*(set(s) for s in by_metric.values())))
    assert len(common) >= 8, "Insufficient measured scrapes"
    t0, t1 = common[0], common[-1]
    actual = kafka_span(
        consumer,
        db,
        int(by_metric["next_offset"][t0]),
        int(by_metric["next_offset"][t1]),
    )
    published_delta = int(by_metric["published"][t1] - by_metric["published"][t0])
    sql_delta = int(by_metric["sql_visible"][t1] - by_metric["sql_visible"][t0])
    assert len(actual) == published_delta
    ids = [
        r[0]
        for r in db.execute(
            """SELECT detection_id FROM observations JOIN occurrences USING(occurrence_id)
       WHERE source_id=? AND run_id=? AND sql_visible_at>? AND sql_visible_at<=?""",
            (SOURCE, RUN, by_metric["watermark"][t0], by_metric["watermark"][t1]),
        )
    ]
    assert len(ids) == sql_delta and sql_delta > 0
    with sql() as remote, remote.cursor() as c:
        c.execute(
            "SELECT count(DISTINCT detection_id) n FROM visions_operator.face_detections WHERE detection_id IN %s",
            (tuple(ids),),
        )
        rows = c.fetchone()["n"]
    assert rows == sql_delta
    return dict(
        first_scrape_utc=datetime.fromtimestamp(t0, timezone.utc).isoformat(),
        last_scrape_utc=datetime.fromtimestamp(t1, timezone.utc).isoformat(),
        scrape_samples=len(common),
        wall_seconds=t1 - t0,
        prometheus_physical_publications=published_delta,
        kafka_messages=len(actual),
        prometheus_distinct_sql=sql_delta,
        starrocks_distinct_rows=rows,
        kafka_rate=published_delta / (t1 - t0),
        sql_rate=sql_delta / (t1 - t0),
    )


def summary():
    report = api("http://127.0.0.1:9125/summary")
    assert report["publication_observer_available"] and report["sql_observer_available"]
    row = next(r for r in report["sources"] if r["source_id"] == SOURCE)
    return dict(
        observed_at=report["observed_at"],
        input_fps=row["observed_input_fps"],
        processed_fps=row["processed_fps"],
        target_fps=row["target_fps"],
        published=row["published_observations_total"],
        sql_visible=row["sql_visible_observations_total"],
        sql_p95_seconds=row["sql_latency"]["p95"],
        completed_missing_frames=row["completed_missing_frames"],
        load_lag=report["routine_load"].get("lag"),
    )


def measure(db, consumer, speed_url, speed, seconds):
    requested_at = time.time()
    api(speed_url, {"speed": speed})

    def at_speed():
        """Waiting for a measured occurrence at the selected playout target"""
        row = db.execute(
            """SELECT speed,frame_count,duration_s,asset_sha256,started_at,scheduled_at FROM occurrences
              WHERE source_id=? AND run_id=? AND state='processing'""",
            (SOURCE, RUN),
        ).fetchone()
        sample = summary()
        return (
            row
            if row
            and sample["input_fps"]
            and sample["processed_fps"]
            and row["scheduled_at"] >= requested_at
            and row["speed"] == speed
            and (speed != 0.5 or row["started_at"] - row["scheduled_at"] < 1.0)
            and abs(
                sample["target_fps"] - speed * row["frame_count"] / row["duration_s"]
            )
            < 0.01
            else None
        )

    print(
        f"Waiting for {speed}× occurrence and fresh measured input; the slow window also requires schedule debt below 1s",
        flush=True,
    )
    active = wait_for(at_speed, timeout=900 if speed == 0.5 else 100)
    begin = snapshot(db)
    print(f"Measuring {SOURCE} at {speed}x for {seconds}s", flush=True)
    until = time.monotonic() + seconds
    samples = {}
    while time.monotonic() < until:
        sample = summary()
        samples[sample["observed_at"]] = sample
        time.sleep(min(4, max(0, until - time.monotonic())))
    end = snapshot(db)
    elapsed = end["at"] - begin["at"]
    records = reconcile_span(db, consumer, begin, end)
    args = (RUN, SOURCE, begin["at"], end["at"])
    rows = db.execute(
        """SELECT f.face_count,o.speed FROM frames f JOIN occurrences o USING(occurrence_id)
             WHERE o.run_id=? AND o.source_id=? AND f.indexed_at>=? AND f.indexed_at<?""",
        args,
    ).fetchall()
    received = db.execute(
        """SELECT count(*) FROM receipts JOIN occurrences USING(occurrence_id)
           WHERE run_id=? AND source_id=? AND observed_at>=? AND observed_at<?""",
        args,
    ).fetchone()[0]
    assert received > 0 and all(s["input_fps"] is not None for s in samples.values()), (
        "Source input receipts unavailable"
    )
    q = db.execute(
        """SELECT o.sql_visible_at,f.ingested_at FROM observations o JOIN frames f USING(occurrence_id,frame_index)
        JOIN occurrences u USING(occurrence_id) WHERE u.run_id=? AND u.source_id=? AND o.sql_visible_at>=? AND o.sql_visible_at<?""",
        args,
    )
    latencies = sorted(
        t - datetime.fromisoformat(ingested).timestamp() for t, ingested in q
    )
    assert latencies
    occurrences = [
        r[0]
        for r in db.execute(
            """SELECT occurrence_id FROM occurrences WHERE run_id=? AND source_id=?
           AND started_at>=? AND finished_at<? AND state='complete'""",
            args,
        )
    ]
    assert occurrences and rows and {r["speed"] for r in rows} == {speed}
    marks = ",".join("?" * len(occurrences))
    expected = db.execute(
        f"""SELECT count(*),sum(face_count),sum(face_count=0),sum(face_count>=2)
           FROM frames WHERE occurrence_id IN ({marks})""",
        occurrences,
    ).fetchone()
    assert (
        expected[0] == len(occurrences) * active["frame_count"]
        and expected[2] > 0
        and expected[3] > 0
    )
    sqlite_ids = {
        r[0]
        for r in db.execute(
            f"SELECT detection_id FROM observations WHERE occurrence_id IN ({marks})",
            occurrences,
        )
    }

    def sql_rows():
        """Waiting for complete-clip StarRocks primary-key rows to reconcile"""
        with sql() as remote, remote.cursor() as cur:
            cur.execute(
                "SELECT detection_id FROM visions_operator.face_detections WHERE source_id IN %s",
                (tuple(occurrences),),
            )
            found = cur.fetchall()
        if len(found) == len(sqlite_ids):
            assert {r["detection_id"] for r in found} == sqlite_ids and len(
                sqlite_ids
            ) == expected[1]
            return len(found)

    actual = wait_for(sql_rows)
    return dict(
        speed=speed,
        native_fps=active["frame_count"] / active["duration_s"],
        asset_sha256=active["asset_sha256"],
        source_frames_per_clip=active["frame_count"],
        speed_requested_at=requested_at,
        initial_occurrence_scheduled_at=active["scheduled_at"],
        initial_schedule_lateness_seconds=active["started_at"] - active["scheduled_at"],
        source_duration_seconds=active["duration_s"],
        wall_seconds=elapsed,
        begin=begin,
        end=end,
        received_frames=received,
        processed_frames=len(rows),
        input_fps=received / elapsed,
        processed_fps=len(rows) / elapsed,
        target_fps=speed * active["frame_count"] / active["duration_s"],
        published=len(records),
        published_per_s=len(records) / elapsed,
        new_sql_visible=end["sql_visible"] - begin["sql_visible"],
        sql_visible_per_s=(end["sql_visible"] - begin["sql_visible"]) / elapsed,
        latency_sql_p95_seconds=latencies[math.ceil(0.95 * len(latencies)) - 1],
        latency_sample_count=len(latencies),
        frame_face_count=dict(sorted(Counter(r["face_count"] for r in rows).items())),
        prometheus_reconciliation=scraped_evidence(db, consumer, begin, end),
        complete_clip_check=dict(
            occurrences=occurrences,
            frames=expected[0],
            zero_face_frames=expected[2],
            multiple_face_frames=expected[3],
            local_distinct_ids=len(sqlite_ids),
            starrocks_logical_rows=actual,
        ),
        exporter_samples=list(samples.values()),
    )


def replay(db, consumer, oid):
    event = db.execute(
        """SELECT detection_id,event_json,kafka_offset,partition_id,sql_visible_at FROM observations
             WHERE occurrence_id=? AND sql_visible_at IS NOT NULL ORDER BY frame_index,detection_id LIMIT 1""",
        (oid,),
    ).fetchone()
    assert event and event["partition_id"] == 0
    did = event["detection_id"]

    def logical_count():
        with sql() as remote, remote.cursor() as cur:
            cur.execute(
                "SELECT count(*) n FROM visions_operator.face_detections WHERE detection_id=%s AND source_id=%s",
                (did, oid),
            )
            return cur.fetchone()["n"]

    assert logical_count() == 1
    before = snapshot(db)
    # The first copy must predate our captured offsets. Only one message is sent, with the saved immutable payload.
    assert event["kafka_offset"] < before["next_offset"]
    producer = KafkaProducer(
        bootstrap_servers=os.getenv("KAFKA_BROKERS", "kafka:9092"), acks="all"
    )
    try:
        sent = producer.send(
            TOPIC, key=did.encode(), value=event["event_json"].encode(), partition=0
        ).get(timeout=10)
    finally:
        producer.close()

    def drained():
        """Waiting for operator Routine Load and publication counter to cross the replay offset"""
        with sql() as remote, remote.cursor() as cur:
            cur.execute("USE visions_operator")
            cur.execute("SHOW ROUTINE LOAD FOR load_detections")
            job = cur.fetchone()
            progress = json.loads(job["Progress"])
        now = snapshot(db)
        return (
            (now, int(progress["0"]))
            if str(progress.get("0")).isdigit()
            and int(progress["0"]) >= sent.offset
            and now["next_offset"] > sent.offset
            else None
        )

    after, progress = wait_for(drained)
    records = reconcile_span(db, consumer, before, after)
    assert [(off, obs) for off, obs in records if obs == did] == [(sent.offset, did)]
    first_seen = db.execute(
        "SELECT sql_visible_at FROM observations WHERE detection_id=?", (did,)
    ).fetchone()[0]
    assert first_seen == event["sql_visible_at"]
    n = logical_count()
    assert n == 1
    return dict(
        detection_id=did,
        occurrence_id=oid,
        topic=TOPIC,
        partition=0,
        first_offset=event["kafka_offset"],
        replay_offset=sent.offset,
        routine_load_progress=progress,
        physical_replayed_messages=1,
        sql_first_visibility_unchanged=True,
        sql_logical_rows_before=1,
        sql_logical_rows_after=n,
        source_counter_before=before,
        source_counter_after=after,
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seconds", type=int, default=75)
    p.add_argument("--operator", default="http://operator-ui:8092")
    args = p.parse_args()
    if args.seconds < 60:
        p.error("Use at least 60 seconds for each measured window")
    path = args.operator + "/api/operator/sources/" + SOURCE + "/speed"
    prior = api(path)["configured"]
    folder = DATA / "throughput"
    folder.mkdir(exist_ok=True, mode=0o700)
    utc = datetime.now(timezone.utc)
    out = folder / (utc.strftime("%Y%m%dT%H%M%SZ") + ".json")
    report = dict(run=RUN, source=SOURCE, started_utc=utc.isoformat(), checks=[])
    consumer = KafkaConsumer(
        bootstrap_servers=os.getenv("KAFKA_BROKERS", "kafka:9092"),
        group_id=None,
        enable_auto_commit=False,
        request_timeout_ms=12000,
    )
    consumer.assign([TopicPartition(TOPIC, 0)])
    try:
        with closing(
            sqlite3.connect(
                (DATA / "feed.sqlite").resolve().as_uri() + "?mode=ro", uri=True
            )
        ) as db:
            db.row_factory = sqlite3.Row
            for speed in (0.5, 1.0):
                report["checks"].append(
                    measure(db, consumer, path, speed, args.seconds)
                )
                out.write_text(json.dumps(report, indent=2) + "\n")
            slow, fast = report["checks"]
            assert slow["asset_sha256"] == fast["asset_sha256"]
            report["replay"] = replay(
                db, consumer, fast["complete_clip_check"]["occurrences"][0]
            )
        report["measured_processed_fps_ratio"] = (
            fast["processed_fps"] / slow["processed_fps"]
        )
        assert (
            fast["processed_fps"] > slow["processed_fps"]
            and fast["input_fps"] > slow["input_fps"]
        ), "Higher target did not increase measured frame throughput"
        report["status"] = "PASS"
    except BaseException as exc:
        report["status"] = (
            "INTERRUPTED" if isinstance(exc, KeyboardInterrupt) else "FAIL"
        )
        report["error"] = type(exc).__name__ + ": " + str(exc)
        raise
    finally:
        consumer.close()
        report["restored_speed"] = api(path, {"speed": prior})["configured"]
        out.write_text(json.dumps(report, indent=2) + "\n")
        print("Report:", out, flush=True)
    print(report["status"], flush=True)


if __name__ == "__main__":
    main()
