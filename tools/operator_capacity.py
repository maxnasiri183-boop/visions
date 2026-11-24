"""Exercise the full operator file feed, recognition worker and filtered search API."""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
from uuid import uuid4


REPO = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    tmp = path.with_suffix(".new")
    tmp.write_text(json.dumps(value, indent=2) + "\n")
    tmp.replace(path)


def link(source, target):
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except OSError:
        shutil.copyfile(source, target)


def clone_gallery(source_db, target_db, ddl):
    """Copy gallery membership and enrollment only. Start every observation index empty."""
    with closing(sqlite3.connect(target_db, uri=True)) as db:
        db.executescript(ddl)
        db.execute("ATTACH DATABASE ? AS source", (source_db.as_uri() + "?mode=ro",))
        db.execute("BEGIN")
        for table in ("refs", "versions", "membership"):
            db.execute(f"INSERT INTO {table} SELECT * FROM source.{table}")
        db.commit()
        assert db.execute("SELECT count(*) FROM indexed").fetchone()[0] == 0
        if not db.execute("SELECT count(*) FROM membership").fetchone()[0]:
            raise ValueError("A published gallery is required")


def query(path, sql, params=()):
    with closing(
        sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=8)
    ) as db:
        db.row_factory = sqlite3.Row
        return db.execute(sql, params).fetchall()


def host_sample():
    with open("/proc/stat") as stat:
        ticks = [int(n) for n in stat.readline().split()[1:9]]
    with open("/proc/meminfo") as mem:
        ram = {line.split(":")[0]: int(line.split()[1]) for line in mem}
    gpu = subprocess.check_output(
        [
            "nvidia-smi",
            "--id=0",
            "--query-gpu=utilization.gpu,memory.used",
            "--format=csv,noheader,nounits",
        ],
        text=True,
        timeout=5,
    )
    usage, vram = map(int, gpu.strip().split(","))
    return dict(
        at=time.time(),
        gpu_pct=usage,
        vram_mib=vram,
        cpu_total=sum(ticks),
        cpu_idle=ticks[3] + ticks[4],
        ram_used_mib=(ram["MemTotal"] - ram["MemAvailable"]) / 1024,
    )


def run_level(args, size, base, docker, seed):
    project = "visions-qa-" + uuid4().hex[:10]
    root = base / f"{size}-streams"
    root.mkdir(mode=0o700)
    cfg = read(base / "frozen-catalog.json")
    write(
        root / "probe-label.json",
        next(
            r
            for r in read(args.source / "research/upload.json")
            if r["gallery_label"] == "enrolled"
        )["people"][0],
    )
    # Copy only stream files and models, never an existing frame database or embedding index.
    for row in cfg:
        link(args.source / row["path"], root / row["path"])
    link(args.source / "models/sface.onnx", root / "models/sface.onnx")
    shutil.copyfile(seed, root / "identity.sqlite")
    labels = {r["path"]: r for r in read(args.source / "research/stream.json")}
    sources = [f"qa-{n}" for n in range(size)]
    env = dict(
        os.environ,
        OPERATOR_ROOT=str(root),
        OPERATOR_DATA_HOST=str(root),
        IDENTITY_YUNET=str(args.yunet),
        YUNET_MODEL_HOST=str(args.yunet),
        FEED_RUN_ID=project,
        FEED_WORKERS=str(size),
        FEED_SOURCE_LIMIT=str(size),
        FEED_RUN_HOURS="1",
        FEED_SPEED="1",
        FEED_RETENTION_SECONDS="1800",
        PYTHONPATH=str(REPO),
    )
    compose_prefix = docker
    if docker[0] == "sudo":
        keep = ",".join(
            k
            for k in env
            if k.startswith("FEED_") or k in ("OPERATOR_DATA_HOST", "YUNET_MODEL_HOST")
        )
        compose_prefix = ["sudo", "-n", "--preserve-env=" + keep, "docker"]
    compose = [
        *compose_prefix,
        "compose",
        "-p",
        project,
        "-f",
        str(args.compose),
        "--env-file",
        str(args.env_file),
    ]

    override = root / "compose.capacity.yaml"
    worker_config = [
        "services:",
        "  operator-worker:",
        "    environment:",
        "      ORT_PROVIDER: " + args.provider,
        '      YUNET_REUSE_INPUT: "true"',
    ]
    if args.worker_image:
        worker_config.append("    image: " + json.dumps(args.worker_image))
    if args.provider == "cpu":
        worker_config.append("    devices: !reset []")
    override.write_text("\n".join(worker_config) + "\n")
    compose.extend(["-f", str(override)])

    def service(*cmd):
        subprocess.run([*compose, *cmd], env=env, stdout=subprocess.DEVNULL, check=True)

    feed, identity = root / "feed.sqlite", root / "identity.sqlite"
    warm = [dict(r, source_id=f"warm-{i}") for i in range(size) for r in cfg]
    write(root / "catalog.json", warm)
    worker_log = (root / "identity-worker.log").open("w")
    identity_worker = subprocess.Popen(
        [sys.executable, __file__, "index-worker"],
        cwd=REPO,
        env=env,
        stdout=worker_log,
        stderr=subprocess.STDOUT,
    )
    samples, searches, seen = [], [], {}
    warm_enqueued = warm_drained = measured = False
    deadline = time.monotonic() + args.timeout
    next_sample = next_search = 0
    search_runner = None
    try:
        service("up", "-d", "--no-build", "operator-worker", "operator-feeder")
        while time.monotonic() < deadline:
            if identity_worker.poll() is not None:
                raise RuntimeError(
                    f"Recognition worker exited; inspect {worker_log.name}"
                )
            if not feed.is_file():
                time.sleep(0.2)
                continue
            try:
                occurrences = query(feed, "SELECT * FROM occurrences ORDER BY sequence")
            except sqlite3.OperationalError as err:
                if "no such table" not in str(err):
                    raise
                time.sleep(0.2)
                continue
            if any(r["attempts"] > 6 for r in occurrences):
                raise RuntimeError(f"Unexpected worker retry in {root}")
            warm_jobs = [r for r in occurrences if r["source_id"].startswith("warm-")]
            if not warm_enqueued and len({r["source_id"] for r in warm_jobs}) == size:
                write(
                    root / "outages.json",
                    {f"warm-{i}": time.time() + args.timeout for i in range(size)},
                )
                warm_enqueued = True
            if (
                warm_enqueued
                and not warm_drained
                and query(
                    feed,
                    "SELECT count(*) FROM service_state WHERE service LIKE 'source:warm-%' AND state='source_outage'",
                )[0][0]
                == size
                and all(r["state"] == "complete" for r in warm_jobs)
            ):
                warm_rows = query(
                    feed,
                    "SELECT count(*) n,count(sql_visible_at) visible,count(search_state) decisions FROM observations",
                )[0]
                index_size = query(identity, "SELECT count(*) n FROM indexed")[0]["n"]
                warm_drained = (
                    warm_rows["n"]
                    == warm_rows["visible"]
                    == warm_rows["decisions"]
                    == index_size
                    and index_size > 0
                )
            if warm_drained and not measured:
                search_runner = SearchClient(root, args.source, env)
                write(
                    root / "catalog.json",
                    [dict(r, source_id=s) for s in sources for r in cfg],
                )
                measured = True
            if not measured:
                time.sleep(0.2)
                continue
            cohort = [r for r in occurrences if r["source_id"] in sources]
            # The regular planner admits at most its configured queue depth. Remove a source
            # from the private QA catalog once its last scheduled occurrence is durable.
            final = {
                r["source_id"]
                for r in cohort
                if r["sequence"] >= len(cfg) * args.cycles - 1
            }
            if final != seen.get("final", set()):
                write(
                    root / "catalog.json",
                    [
                        dict(r, source_id=s)
                        for s in sources
                        if s not in final
                        for r in cfg
                    ],
                )
                seen["final"] = final
            now = time.monotonic()
            if cohort and now >= next_sample:
                sample = host_sample()
                count = query(
                    feed,
                    """SELECT count(*) n,count(sql_visible_at) visible
                    FROM observations JOIN occurrences USING(occurrence_id) WHERE source_id LIKE 'qa-%'""",
                )[0]
                idx = query(
                    identity,
                    "SELECT count(*) n FROM indexed WHERE source_id LIKE 'qa-%'",
                )[0]["n"]
                sample.update(
                    pending_identity=max(0, count["n"] - idx),
                    pending_sql=count["n"] - count["visible"],
                    queued=sum(r["state"] == "queued" for r in cohort),
                    disk_free_bytes=shutil.disk_usage(root).free,
                )
                samples.append(sample)
                next_sample = now + 1
            if cohort and now >= next_search:
                searches.append(
                    search_runner.search(sources[len(searches) % size], cohort)
                )
                next_search = now + 5
            if len(final) == size and all(r["state"] == "complete" for r in cohort):
                counts = query(
                    feed,
                    "SELECT count(*) n,count(sql_visible_at) visible,count(search_state) decisions FROM observations",
                )[0]
                indexed = query(identity, "SELECT count(*) n FROM indexed")[0]["n"]
                if counts["n"] == counts["visible"] == counts["decisions"] == indexed:
                    break
            time.sleep(0.2)
        else:
            raise TimeoutError(
                f"QA pipeline failed to drain within {args.timeout}s. Inspect {root}."
            )
        searches.append(search_runner.search(sources[0], cohort))
        from operator_capacity_report import summarize

        write(root / "samples.json", samples)
        write(root / "searches.json", searches)
        result = summarize(root, size, cfg, labels, samples, searches, args.cycles)
        result["detector_provider"] = args.provider
        result["images"] = {}
        for service_name in ("operator-feeder", "operator-worker"):
            container = subprocess.check_output(
                compose + ["ps", "-q", service_name], text=True, env=env
            ).strip()
            if service_name == "operator-worker" and args.provider == "cpu":
                settings = json.loads(
                    subprocess.check_output([*docker, "inspect", container], text=True)
                )[0]["HostConfig"]
                if settings.get("Devices") or settings.get("DeviceRequests"):
                    raise ValueError("CPU worker unexpectedly received GPU devices")
                result["worker_gpu_devices"] = 0
            result["images"][service_name] = subprocess.check_output(
                [*docker, "inspect", "-f", "{{.Image}}", container], text=True
            ).strip()
        write(root / "summary.json", result)
        return result
    finally:
        if search_runner:
            search_runner.close()
        identity_worker.terminate()
        try:
            identity_worker.wait(timeout=10)
        except subprocess.TimeoutExpired:
            identity_worker.kill()
            identity_worker.wait()
        worker_log.close()
        with (root / "feed-worker.log").open("w") as log:
            subprocess.run(
                compose + ["logs", "--no-color", "operator-worker"], env=env, stdout=log
            )
        # Remove only this unique QA Compose project. Its SQLite evidence stays private.
        service("down", "--timeout", "5")


class SearchClient:
    """Call the same Flask handlers as the product against this level's data root."""

    def __init__(self, root, source, env):
        self.process = subprocess.Popen(
            [sys.executable, __file__, "query-worker", str(source)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            env=env,
        )
        if self.process.stdout.readline().strip() != "ready":
            self.close()
            raise RuntimeError("Could not prepare held-out upload probe")
        self.root = root

    def search(self, camera, cohort):
        now = time.time()
        request = dict(
            source_id=camera,
            from_utc=datetime.fromtimestamp(
                min(r["scheduled_at"] for r in cohort), timezone.utc
            ).isoformat(),
            to_utc=datetime.fromtimestamp(now, timezone.utc).isoformat(),
        )
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        result = json.loads(self.process.stdout.readline())
        return dict(result, at=now)

    def close(self):
        self.process.stdin.close()
        self.process.wait(timeout=15)


def index_worker():
    """Record source-frame retrieval cost around the deployed recognition implementation."""
    sys.path.insert(0, str(REPO))
    from identity import worker

    original_frame = worker.Decoder.frame
    path = Path(os.environ["OPERATOR_ROOT"]) / "recognition-decode.jsonl"
    with path.open("w", buffering=1) as out:

        def measured_frame(decoder, occurrence, source_path, frame):
            before = time.perf_counter()
            result = original_frame(decoder, occurrence, source_path, frame)
            out.write(
                json.dumps([occurrence, frame, (time.perf_counter() - before) * 1000])
                + "\n"
            )
            return result

        worker.Decoder.frame = measured_frame
        sys.argv = sys.argv[:1]
        worker.main()


def query_worker(source):
    sys.path.insert(0, str(REPO))
    from flask import Flask
    from identity.api import bp

    app = Flask(__name__)
    app.register_blueprint(bp)
    client = app.test_client()
    item = next(
        r
        for r in read(source / "research/upload.json")
        if r["gallery_label"] == "enrolled"
    )
    with (source / item["path"]).open("rb") as fp:
        resp = client.post("/api/identity/probes", data={"file": (fp, "held-out.jpg")})
    assert (
        resp.status_code == 201
        and len(resp.json["faces"]) == 1
        and not resp.json["faces"][0]["reason"]
    )
    probe = resp.json["probe_id"]
    print("ready", flush=True)
    for line in sys.stdin:
        body = json.loads(line)
        start = time.monotonic()
        reply = client.post(
            "/api/identity/search",
            json=dict(body, probe_id=probe, face_index=0, limit=5),
        )
        result = reply.get_json()
        if reply.status_code != 200:
            raise ValueError(result)
        groups = result["groups"]
        assert all(g["source_id"] == body["source_id"] for g in groups)
        print(
            json.dumps(
                dict(
                    elapsed_ms=(time.monotonic() - start) * 1000,
                    candidates=result["candidate_count"],
                    hits=len(groups),
                    top_detection=groups[0]["observations"][0]["detection_id"]
                    if groups
                    else None,
                )
            ),
            flush=True,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Existing private operator corpus and published gallery",
    )
    parser.add_argument("--yunet", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="New, private directory for QA stores",
    )
    parser.add_argument("--compose", type=Path, default=REPO / "compose.feed.yaml")
    parser.add_argument("--env-file", type=Path, default=REPO / ".env")
    parser.add_argument(
        "--streams", type=int, nargs="+", choices=[1, 2, 4, 8], default=[1, 2, 4, 8]
    )
    parser.add_argument("--worker-image", help="Use a specific QA worker image")
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--provider", choices=("cuda", "cpu"), default="cuda")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument(
        "--background-feed-db",
        type=Path,
        help="Measure concurrent production feed frames in each window",
    )
    args = parser.parse_args()
    if args.cycles < 1:
        parser.error("cycles must be positive")
    for key in ("source", "output", "compose", "env_file", "yunet"):
        setattr(args, key, getattr(args, key).resolve())
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    sys.path.insert(0, str(REPO))
    from identity.core import DDL

    write(args.output / "frozen-catalog.json", read(args.source / "catalog.json"))
    seed = args.output / "gallery-only.sqlite"
    clone_gallery(args.source / "identity.sqlite", seed, DDL)
    docker = ["docker"]
    if subprocess.run(docker + ["info"], capture_output=True).returncode:
        docker = ["sudo", "-n", "docker"]
    results = []
    for size in args.streams:
        result = run_level(args, size, args.output, docker, seed)
        from operator_capacity_report import runtime_evidence

        trial = args.output / f"{size}-streams"
        runtime_evidence(
            trial,
            result,
            args.background_feed_db.resolve() if args.background_feed_db else None,
        )
        write(trial / "summary.json", result)
        results.append(result)
        print(
            json.dumps(
                {
                    k: result[k]
                    for k in (
                        "streams",
                        "frames",
                        "detections",
                        "full_rate_fps",
                        "target_fps",
                        "meets_target",
                    )
                }
            ),
            flush=True,
        )
        write(args.output / "matrix.json", results)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "query-worker":
        query_worker(Path(sys.argv[2]))
    elif len(sys.argv) > 1 and sys.argv[1] == "index-worker":
        index_worker()
    else:
        main()
