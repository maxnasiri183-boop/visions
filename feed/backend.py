"""Private operator namespace and observed SQL visibility."""

import json
import os
import shutil
import time
from contextlib import closing

import boto3
import pymysql
from botocore.config import Config
from botocore.exceptions import ClientError
from kafka.admin import KafkaAdminClient, NewTopic
from kafka.errors import TopicAlreadyExistsError
from db import DATA, connect, local, state

BUCKET = "visions-operator"
TOPIC = "face.operator.v1"
RETENTION = int(os.getenv("FEED_RETENTION_SECONDS", "3600"))


def store():
    return boto3.client(
        "s3",
        endpoint_url=os.getenv("S3_ENDPOINT", "http://s3:9000"),
        region_name="us-east-1",
        config=Config(
            proxies={},
            connect_timeout=3,
            read_timeout=12,
            retries={"max_attempts": 2},
            s3={"addressing_style": "path"},
        ),
    )


def sql():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "starrocks"),
        port=int(os.getenv("DB_PORT", "9030")),
        user="root",
        autocommit=True,
        connect_timeout=3,
        read_timeout=12,
        cursorclass=pymysql.cursors.DictCursor,
    )


def provision():
    s3 = store()
    try:
        s3.create_bucket(Bucket=BUCKET)
    except ClientError as e:
        if e.response["Error"]["Code"] != "BucketAlreadyOwnedByYou":
            raise
    # Orphaned crops after hard crashes are protected by the bucket lifecycle.
    s3.put_bucket_lifecycle_configuration(
        Bucket=BUCKET,
        LifecycleConfiguration={
            "Rules": [
                {
                    "ID": "OperatorOrphanExpiry",
                    "Filter": {"Tag": {"Key": "retention", "Value": "rolling"}},
                    "Status": "Enabled",
                    "Expiration": {"Days": 1},
                }
            ]
        },
    )
    with closing(
        KafkaAdminClient(bootstrap_servers=os.getenv("KAFKA_BROKERS", "kafka:9092"))
    ) as admin:
        try:
            admin.create_topics(
                [
                    NewTopic(
                        TOPIC,
                        1,
                        1,
                        topic_configs={
                            "retention.ms": "7200000",
                            "segment.ms": "300000",
                            "retention.bytes": str(256 * 1024**2),
                        },
                    )
                ]
            )
        except TopicAlreadyExistsError:
            pass
    with sql() as db, db.cursor() as c:
        text = (
            open("/feed/detections.sql")
            .read()
            .replace("visions.", "visions_operator.")
            .replace("EXISTS visions;", "EXISTS visions_operator;")
            .replace("USE visions;", "USE visions_operator;")
            .replace("face.detected.v1", TOPIC)
        )
        for statement in text.strip().rstrip(";").split(";"):
            if statement.strip().startswith("CREATE ROUTINE LOAD"):
                c.execute("SHOW ROUTINE LOAD")
                if any(x["Name"] == "load_detections" for x in c.fetchall()):
                    continue
            c.execute(statement)
    s3.close()


def visibility_loop():
    local_db = connect()
    while True:
        try:
            with sql() as db, db.cursor() as c:
                while True:
                    ids = [
                        r[0]
                        for r in local_db.execute(
                            "SELECT detection_id FROM observations WHERE sql_visible_at IS NULL LIMIT 512"
                        )
                    ]
                    if ids:
                        c.execute(
                            "SELECT * FROM visions_operator.face_detections WHERE detection_id IN %s",
                            (tuple(ids),),
                        )
                        rows = c.fetchall()
                        now = time.time()
                        local_db.execute("BEGIN")
                        for row in rows:
                            local_db.execute(
                                "UPDATE observations SET sql_visible_at=?,sql_row=? "
                                "WHERE detection_id=? AND sql_visible_at IS NULL",
                                (
                                    now,
                                    json.dumps(row, default=str),
                                    row["detection_id"],
                                ),
                            )
                        local_db.commit()
                    state(local_db, "sql-observer", "active")
                    time.sleep(0.25)
        except Exception as error:
            local_db.rollback()
            state(
                local_db,
                "sql-observer",
                "unavailable",
                type(error).__name__ + ": " + str(error)[:400],
            )
            time.sleep(2)


def search_visibility_loop():
    db = connect()
    while not (DATA / "identity.sqlite").exists():
        state(db, "search-observer", "waiting_index")
        time.sleep(1)
    db.execute("ATTACH DATABASE ? AS identity", (str(DATA / "identity.sqlite"),))
    while True:
        try:
            rows = db.execute(
                "SELECT ob.detection_id,i.state,(i.embedding IS NOT NULL) AS has_vector "
                "FROM observations ob JOIN identity.indexed i USING(detection_id) "
                "WHERE ob.search_state IS NULL LIMIT 1024"
            ).fetchall()
            now = time.time()
            db.execute("BEGIN IMMEDIATE")
            for row in rows:
                ready = row["state"] == "ready" and row["has_vector"]
                db.execute(
                    "UPDATE observations SET search_state=?,search_visible_at=? WHERE detection_id=?",
                    (row["state"], now if ready else None, row["detection_id"]),
                )
            db.commit()
            state(db, "search-observer", "active")
            time.sleep(0.2 if len(rows) < 1024 else 0.02)
        except Exception as error:
            db.rollback()
            state(db, "search-observer", "unavailable", str(error)[:400])
            time.sleep(2)


def prune_media(db):
    catalog = DATA / "catalog.json"
    if not catalog.exists():
        return
    assets = json.loads(catalog.read_text())
    if any(not item.get("sha256") for item in assets):
        return
    keep = {item["sha256"] for item in assets}
    for item in db.execute("SELECT * FROM media_assets").fetchall():
        if (
            item["checksum"] in keep
            or db.execute(
                "SELECT 1 FROM occurrences WHERE asset_sha256=? "
                "AND state!='expired' LIMIT 1",
                (item["checksum"],),
            ).fetchone()
        ):
            continue
        for field in ("media_path", "input_path"):
            asset = local(item[field])
            if asset.parent != DATA / "media" / item["checksum"]:
                raise ValueError("Refusing unexpected derived media path")
            asset.unlink(missing_ok=True)
        db.execute("DELETE FROM media_assets WHERE checksum=?", (item["checksum"],))


def protect_pins(db, s3, pins):
    db.execute(
        "CREATE TABLE IF NOT EXISTS protected_pins(occurrence_id TEXT PRIMARY KEY)"
    )
    done = {r[0] for r in db.execute("SELECT occurrence_id FROM protected_pins")}
    for oid in set(pins) - done:
        if not db.execute(
            "SELECT 1 FROM occurrences WHERE occurrence_id=? AND state='complete'",
            (oid,),
        ).fetchone():
            continue
        keys = [
            "crops/" + r[0] + ".png"
            for r in db.execute(
                "SELECT detection_id FROM observations WHERE occurrence_id=?", (oid,)
            )
        ]
        keys.extend(
            r[0]
            for r in db.execute(
                "SELECT manifest_key FROM frames WHERE occurrence_id=?", (oid,)
            )
        )
        for key in keys:
            s3.put_object_tagging(
                Bucket=BUCKET,
                Key=key,
                Tagging={"TagSet": [{"Key": "retention", "Value": "pinned"}]},
            )
        db.execute("INSERT INTO protected_pins VALUES(?)", (oid,))
    for oid in done - set(pins):
        db.execute("DELETE FROM protected_pins WHERE occurrence_id=?", (oid,))


def expire():
    db, s3 = connect(), store()
    # Only completed, owned occurrences past the retention window are eligible.
    while True:
        try:
            cutoff = time.time() - RETENTION
            pins_path = DATA / "pinned.json"
            pins = json.loads(pins_path.read_text()) if pins_path.exists() else []
            if (
                not isinstance(pins, list)
                or len(pins) > 8
                or any(not isinstance(x, str) or not x.startswith("occ-") for x in pins)
            ):
                raise ValueError("Expected up to eight pinned occurrence IDs")
            protect_pins(db, s3, pins)
            rows = db.execute(
                "SELECT occurrence_id,run_id FROM occurrences WHERE "
                "(state='complete' AND finished_at<?) OR state='expiring' ORDER BY finished_at LIMIT 24",
                (cutoff,),
            ).fetchall()
            rows = [row for row in rows if row["occurrence_id"] not in pins]
            for row in rows:
                oid = row["occurrence_id"]
                if not oid.startswith("occ-"):
                    raise ValueError("Refusing unowned retention key")
                offsets = db.execute(
                    "SELECT partition_id,max(latest_offset) FROM observations WHERE occurrence_id=? GROUP BY partition_id",
                    (oid,),
                ).fetchall()
                if offsets:
                    with sql() as remote, remote.cursor() as c:
                        c.execute("USE visions_operator")
                        c.execute("SHOW ROUTINE LOAD FOR load_detections")
                        job = c.fetchone()
                        progress = json.loads(job["Progress"])
                        if any(
                            not str(progress.get(str(part), "")).isdigit()
                            or int(progress[str(part)]) < offset
                            for part, offset in offsets
                        ):
                            state(
                                db,
                                "retention",
                                "awaiting_sql",
                                "Routine Load has not crossed the occurrence replay offset",
                            )
                            continue
                db.execute(
                    "UPDATE occurrences SET state='expiring' WHERE occurrence_id=?",
                    (oid,),
                )
                keys = [
                    {"Key": "crops/" + r[0] + ".png"}
                    for r in db.execute(
                        "SELECT detection_id FROM observations WHERE occurrence_id=?",
                        (oid,),
                    )
                ]
                for page in s3.get_paginator("list_objects_v2").paginate(
                    Bucket=BUCKET, Prefix="frames/" + oid + "/"
                ):
                    keys.extend({"Key": x["Key"]} for x in page.get("Contents", []))
                for index in range(0, len(keys), 500):
                    result = s3.delete_objects(
                        Bucket=BUCKET,
                        Delete={"Objects": keys[index : index + 500], "Quiet": True},
                    )
                    if result.get("Errors"):
                        raise RuntimeError("Object expiry failed")
                with sql() as remote, remote.cursor() as c:
                    c.execute(
                        "DELETE FROM visions_operator.face_detections WHERE source_id=%s",
                        (oid,),
                    )
                crops = local("crops/" + oid)
                if crops.exists():
                    shutil.rmtree(crops)
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    """INSERT OR REPLACE INTO expiry_counts SELECT ?,count(*),
                    coalesce(sum(sql_visible_at IS NULL),0),coalesce(sum(search_state IS NULL),0),
                    coalesce(sum(search_state='error'),0) FROM observations WHERE occurrence_id=?""",
                    (oid, oid),
                )
                db.execute("DELETE FROM observations WHERE occurrence_id=?", (oid,))
                db.execute("DELETE FROM frames WHERE occurrence_id=?", (oid,))
                db.execute("DELETE FROM receipts WHERE occurrence_id=?", (oid,))
                db.execute("DELETE FROM replay_events WHERE occurrence_id=?", (oid,))
                db.execute(
                    "UPDATE occurrences SET state='expired',expired_at=?,input_path='',media_path='' "
                    "WHERE occurrence_id=?",
                    (time.time(), oid),
                )
                db.execute(
                    "UPDATE run_totals SET expired=expired+1 WHERE run_id=?",
                    (row["run_id"],),
                )
                db.commit()
            prune_media(db)
            state(db, "retention", "active", str(RETENTION) + " s")
            time.sleep(1 if rows else 8)
        except Exception as error:
            db.rollback()
            state(db, "retention", "error", str(error)[:400])
            time.sleep(5)
