"""Durable source counters. SQL visibility and Kafka offset checkpoints survive retention."""

from collections import Counter
from contextlib import closing
import json
import os
import time

from backend import TOPIC
from db import connect


def install(db):
    db.execute("BEGIN IMMEDIATE")
    try:
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='throughput_first_sql'"
        ).fetchone():
            db.commit()
            return
        db.execute("""CREATE TABLE throughput_source(
            run_id TEXT NOT NULL, source_id TEXT NOT NULL, published INTEGER NOT NULL DEFAULT 0,
            sql_visible INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(run_id,source_id))""")
        db.execute("""CREATE TABLE throughput_cursor(
            partition_id INTEGER PRIMARY KEY,next_offset INTEGER NOT NULL,started_at REAL NOT NULL,
            missed_offsets INTEGER NOT NULL DEFAULT 0,unmapped INTEGER NOT NULL DEFAULT 0)""")
        # Retention moves rows into expiry_counts and removes the observations in one transaction.
        db.execute("""INSERT INTO throughput_source(run_id,source_id,sql_visible)
            SELECT run_id,source_id,sum(n) FROM (
              SELECT run_id,source_id,count(*) AS n FROM observations JOIN occurrences USING(occurrence_id)
              WHERE sql_visible_at IS NOT NULL GROUP BY run_id,source_id
              UNION ALL
              SELECT run_id,source_id,sum(detections-missing_sql) FROM expiry_counts JOIN occurrences
              USING(occurrence_id) GROUP BY run_id,source_id
            ) GROUP BY run_id,source_id""")
        db.execute("""CREATE TRIGGER throughput_first_sql AFTER UPDATE OF sql_visible_at ON observations
            WHEN OLD.sql_visible_at IS NULL AND NEW.sql_visible_at IS NOT NULL
            BEGIN
              INSERT INTO throughput_source(run_id,source_id,sql_visible)
              SELECT run_id,source_id,1 FROM occurrences WHERE occurrence_id=NEW.occurrence_id
              ON CONFLICT(run_id,source_id) DO UPDATE SET sql_visible=sql_visible+1;
            END""")
        db.commit()
    except BaseException:
        db.rollback()
        raise


def start_cursor(db, part, earliest, latest):
    with db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "INSERT OR IGNORE INTO throughput_cursor(partition_id,next_offset,started_at) VALUES(?,?,?)",
            (part, latest, time.time()),
        )
        offset = db.execute(
            "SELECT next_offset FROM throughput_cursor WHERE partition_id=?", (part,)
        ).fetchone()[0]
        if offset > latest:
            raise RuntimeError("Kafka end offset precedes the durable checkpoint")
        if offset < earliest:
            db.execute(
                "UPDATE throughput_cursor SET next_offset=?,missed_offsets=missed_offsets+? WHERE partition_id=?",
                (earliest, earliest - offset, part),
            )
    return max(offset, earliest)


def account_records(db, part, records):
    """Count each physical record once, including republished detection IDs."""
    with db:
        db.execute("BEGIN IMMEDIATE")
        offset = db.execute(
            "SELECT next_offset FROM throughput_cursor WHERE partition_id=?", (part,)
        ).fetchone()[0]
        increments = Counter()
        next_offset = offset
        unmapped = 0
        for record in records:
            if record.offset < offset:
                continue
            next_offset = record.offset + 1
            try:
                payload = json.loads(record.value)
                if not isinstance(payload.get("detection_id"), str) or not isinstance(
                    payload.get("source_id"), str
                ):
                    raise ValueError("Unrecognized observation")
                increments[payload["source_id"]] += 1
            except (ValueError, AttributeError, TypeError, UnicodeDecodeError):
                unmapped += 1
        for occurrence, n in increments.items():
            source = db.execute(
                "SELECT run_id,source_id FROM occurrences WHERE occurrence_id=?",
                (occurrence,),
            ).fetchone()
            if source:
                db.execute(
                    """INSERT INTO throughput_source(run_id,source_id,published) VALUES(?,?,?)
                    ON CONFLICT(run_id,source_id) DO UPDATE SET published=published+excluded.published""",
                    (*source, n),
                )
            else:
                unmapped += n
        db.execute(
            "UPDATE throughput_cursor SET next_offset=?,unmapped=unmapped+? WHERE partition_id=?",
            (next_offset, unmapped, part),
        )


def monitor_publications(status):
    from kafka import KafkaConsumer, TopicPartition

    while True:
        try:
            with closing(connect()) as db:
                install(db)
                broker = os.getenv("KAFKA_BROKERS", "kafka:9092")
                consumer = KafkaConsumer(
                    bootstrap_servers=broker,
                    group_id=None,
                    enable_auto_commit=False,
                    auto_offset_reset="none",
                    request_timeout_ms=12000,
                    client_id="operator-throughput",
                )
                with closing(consumer):
                    partitions = consumer.partitions_for_topic(TOPIC)
                    if not partitions:
                        raise RuntimeError("Operator Kafka topic unavailable")
                    assigned = [TopicPartition(TOPIC, p) for p in sorted(partitions)]
                    consumer.assign(assigned)
                    low, high = (
                        consumer.beginning_offsets(assigned),
                        consumer.end_offsets(assigned),
                    )
                    for tp in assigned:
                        consumer.seek(
                            tp, start_cursor(db, tp.partition, low[tp], high[tp])
                        )
                    next_probe = 0
                    while True:
                        batches = consumer.poll(timeout_ms=500, max_records=500)
                        for tp, records in batches.items():
                            account_records(db, tp.partition, records)
                        if time.monotonic() >= next_probe:
                            high = consumer.end_offsets(assigned)
                            lag = sum(
                                max(0, high[tp] - consumer.position(tp))
                                for tp in assigned
                            )
                            status.update(observed_at=time.time(), lag=lag, error=None)
                            next_probe = time.monotonic() + 4
        except Exception as error:
            status.update(error=type(error).__name__, observed_at=0)
            print(
                f"Publication observer unavailable: {type(error).__name__}", flush=True
            )
            time.sleep(3)
