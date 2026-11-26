"""Compare the pre-packaging Compose model with both supported paths to each stack."""

import argparse
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
STACKS = (
    ("compose",), ("compose", "gpu"), ("compose", "monitoring"),
    ("compose", "gpu", "monitoring"), ("compose", "monitoring", "gpu"),
    ("compose", "gpu", "t4"), ("compose", "gpu", "t4", "monitoring"),
    ("feed",), ("feed", "feed.monitoring"), ("feed", "feed.monitoring", "feed.rtsp"),
    ("feed", "feed.monitoring", "feed.rtsp", "t4/feed"),
    ("operator",), ("operator", "t4/operator"), ("gallery-scale",), ("rtsp",),
)


def filename(name, canonical):
    if name.startswith("t4/"):
        return ("infra/" if canonical else "deploy/") + name + ".yaml"
    base = "compose" if name == "compose" else "compose." + name
    return ("infra/compose/" if canonical else "") + base + ".yaml"


def model(root, names, env_file, canonical=False):
    command = [str(root / "tools/compose")] if canonical else ["docker", "compose"]
    command += ["--env-file", str(env_file), "--profile", "*"]
    for name in names:
        command += ["-f", filename(name, canonical)]
    # Use a synthetic environment only. Never dump resolved service credentials.
    env = {key: os.environ[key] for key in ("HOME", "PATH") if key in os.environ}
    raw = subprocess.check_output(
        command + ["config", "--format", "json"],
        cwd=env_file.parent if canonical else root, env=env,
    )
    result = json.loads(raw.decode().replace(str(root), "<checkout>"))
    return {k: v for k, v in result.items() if not k.startswith("x-")}


def migrated(old, names):
    dockerfiles = {
        "Dockerfile": "apps/detection-worker/Dockerfile",
        "Dockerfile.gpu": "apps/detection-worker/Dockerfile.gpu",
        "feed/Dockerfile": "apps/feed/Dockerfile",
        "operator/Dockerfile": "apps/operator/Dockerfile",
        "web/Dockerfile": "apps/review/Dockerfile",
    }
    for name, service in old["services"].items():
        build = service.get("build")
        if build:
            build["dockerfile"] = dockerfiles[build["dockerfile"]]
        for mount in service.get("volumes", []):
            mount["source"] = mount["source"].replace("<checkout>/monitoring/", "<checkout>/infra/monitoring/")
            if mount["source"] == "<checkout>/feedsimulator":
                mount["source"] = "<checkout>/apps/feed-simulator"
        if "t4/feed" in names:
            assert build["target"] == "python"
            build["target"] = "worker"
            del build["args"]["REVIEW_IMAGE"]
            build["args"]["DETECTOR_IMAGE"] = "visions-detector-gpu:latest"
            if name != "operator-worker":
                service["entrypoint"] = ["python"]
    return old


def check(before):
    with TemporaryDirectory() as directory:
        temp = Path(directory)
        baseline = temp / "old" / ROOT.name
        baseline.mkdir(parents=True)
        # Only the Compose models are needed from the old revision.
        files = {filename(n, False) for stack in STACKS for n in stack}
        for name in files:
            dest = baseline / name
            dest.parent.mkdir(exist_ok=True, parents=True)
            dest.write_bytes(subprocess.check_output(["git", "show", before + ":" + name], cwd=ROOT))
        fixture = temp / "stack.env"
        fixture.write_text("S3_PASSWORD=disposable\nFEED_RUN_ID=check\nRTSP_CAMERAS={}\n"
                           "OPERATOR_DATA_HOST=/example/retained\nYUNET_MODEL_HOST=/example/model\n")
        for stack in STACKS:
            expected = migrated(model(baseline, stack, fixture), stack)
            assert model(ROOT, stack, fixture) == expected, ("legacy", stack)
            assert model(ROOT, stack, fixture, canonical=True) == expected, ("canonical", stack)
    print(f"PASS {len(STACKS)} complete Compose configurations through both entry points")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", default="b5eee3fad125ced11c52866fca66972e81c56e86")
    check(parser.parse_args().before)
