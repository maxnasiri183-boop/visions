"""Expire this benchmark's remote objects and SQL rows using the feed's retention worker."""

import argparse
from contextlib import closing
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time

from operator_capacity import query


def expire(trial, compose, env_file, yunet):
    feed = trial / "feed.sqlite"
    project_rows = query(feed, "SELECT DISTINCT run_id FROM runs")
    if len(project_rows) != 1 or not re.fullmatch(
        "visions-qa-[0-9a-f]{10}", project_rows[0][0]
    ):
        raise ValueError(
            "Expected a database created exclusively by operator_capacity.py"
        )
    project = project_rows[0][0]
    if any(
        not (row[0].startswith(("qa-", "warm-")) and row[1] == project)
        for row in query(feed, "SELECT DISTINCT source_id,run_id FROM occurrences")
    ):
        raise ValueError("Unexpected source or run in QA database")
    if (trial / "pinned.json").exists():
        raise ValueError("This benchmark does not create pins")
    docker = ["docker"]
    if subprocess.run(docker + ["info"], capture_output=True).returncode:
        docker = ["sudo", "-n", "docker"]
    live = subprocess.check_output(
        [
            *docker,
            "ps",
            "-q",
            "--filter",
            f"label=com.docker.compose.project={project}",
        ],
        text=True,
    ).strip()
    if live:
        raise ValueError("QA project must finish before expiry")
    # Preserve observation timings and measured detector outputs locally for audit.
    backup = trial / "evidence-feed.sqlite"
    with closing(sqlite3.connect(feed)) as db:
        if not backup.exists():
            with closing(sqlite3.connect(backup)) as dest:
                db.backup(dest)
        # Only this harness's interrupted private work can be retired here.
        # The deployed expiry path checks every observed Kafka offset before deleting.
        with db:
            db.execute(
                "UPDATE occurrences SET state='complete',finished_at=0 WHERE state NOT IN ('expired','expiring','complete')"
            )

    args = [*docker]
    if args[0] == "sudo":
        args = [
            "sudo",
            "-n",
            "--preserve-env=FEED_RUN_ID,OPERATOR_DATA_HOST,YUNET_MODEL_HOST",
            "docker",
        ]
    args.extend(
        ["compose", "-p", project, "-f", str(compose), "--env-file", str(env_file)]
    )
    env = dict(
        os.environ,
        FEED_RUN_ID=project,
        OPERATOR_DATA_HOST=str(trial),
        YUNET_MODEL_HOST=str(yunet),
    )
    container = project + "-expire"
    subprocess.run(
        [
            *args,
            "run",
            "-d",
            "--no-deps",
            "--name",
            container,
            "--entrypoint",
            "python3.12",
            "-e",
            "FEED_RETENTION_SECONDS=0",
            "operator-worker",
            "-c",
            "from backend import expire; expire()",
        ],
        env=env,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    try:
        stop = time.monotonic() + 240
        while time.monotonic() < stop:
            if not query(
                feed, "SELECT 1 FROM occurrences WHERE state!='expired' LIMIT 1"
            ):
                print(f"Expired the QA project at {trial}", flush=True)
                return
            time.sleep(2)
        raise TimeoutError(f"Inspect QA retention notices in {feed}")
    finally:
        subprocess.run(
            [*docker, "rm", "-f", container], check=True, stdout=subprocess.DEVNULL
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trial", type=Path, nargs="+")
    parser.add_argument("--compose", type=Path, default=Path("compose.feed.yaml"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--yunet", type=Path, required=True)
    args = parser.parse_args()
    for trial in args.trial:
        expire(
            trial.resolve(),
            args.compose.resolve(),
            args.env_file.resolve(),
            args.yunet.resolve(),
        )
