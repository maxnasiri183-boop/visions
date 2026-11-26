"""Read the retained Kafka, S3 and SQL evidence used by Review and the acceptance runner."""

import json
import os
from pathlib import Path
import time

import boto3
import pymysql
from botocore.config import Config
from botocore.exceptions import ClientError
from kafka import KafkaConsumer, TopicPartition
from kafka.admin import KafkaAdminClient, NewTopic
from kafka.errors import TopicAlreadyExistsError

TOPIC = "face.detected.v1"
BUCKET = "visions-private"
BROKER = os.getenv("BROKER", "127.0.0.1:19092")


def storage():
    password = os.getenv("S3_PASSWORD") or next(
        line.split("=", 1)[1]
        for line in Path(os.environ.get("VISIONS_ENV_FILE", ".env")).read_text().splitlines()
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
            Path(os.environ.get("VISIONS_DETECTIONS_SQL", "infra/starrocks/detections.sql")).read_text().strip().rstrip(";").split(";")
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
