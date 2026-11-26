"""Check what a full-context COPY can see for each Visions Dockerfile."""

from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]

# Use synthetic files. No workspace data needs to be sent to Docker for this check.
CONTEXTS = {
    ".dockerignore": {
        "settings.gradle", "build.gradle", "libs/contracts/build.gradle",
        "libs/contracts/src/main/java/visions/Detection.java", "libs/vision-onnx/build.gradle",
        "libs/vision-onnx/src/test/java/visions/YuNetTest.java", "apps/detection-worker/build.gradle",
        "apps/detection-worker/src/main/java/visions/worker/DetectionWorker.java",
    },
    "apps/feed/Dockerfile.dockerignore": {
        "apps/feed/pyproject.toml", "apps/feed/src/visions_feed/worker.py",
        "infra/starrocks/detections.sql",
    },
    "apps/operator/Dockerfile.dockerignore": {
        "apps/operator/pyproject.toml", "apps/operator/src/identity/worker.py",
        "apps/operator/src/visions_operator/server.py", "apps/operator/src/visions_operator/dist/index.html",
        "apps/operator/src/visions_operator/dist/assets/index.js", "research/identity/pyproject.toml",
        "research/identity/src/identity/evaluate.py",
    },
    "apps/review/Dockerfile.dockerignore": {
        "apps/review/pyproject.toml", "apps/review/src/visions_review/app.py",
        "apps/review/src/visions_review/static/review.js", "infra/starrocks/detections.sql",
    },
}
EXCLUDED = {
    ".env", "private/identity.sqlite", "feed/sample_irdb/input.jpg", "feed/sample_videos/input.mp4",
    "apps/operator/frontend/node_modules/example/index.js", "apps/feed/src/visions_feed/data.sqlite",
    "apps/detection-worker/build/libs/worker.jar", "libs/contracts/.gradle/cache.bin",
    "infra/t4/site.env", "apps/another-worker/serve.py", "research/portraits/samples.py",
}


def main():
    with TemporaryDirectory() as temp:
        directory = Path(temp)
        context = directory / "sample"
        for name in EXCLUDED.union(*CONTEXTS.values()):
            sample = context / name
            sample.parent.mkdir(parents=True, exist_ok=True)
            sample.write_text(name)
        for index, (ignore, expected) in enumerate(CONTEXTS.items()):
            (context / ".dockerignore").write_bytes((ROOT / ignore).read_bytes())
            destination = directory / str(index)
            subprocess.run(
                ["docker", "buildx", "build", "--file", "-", "--output",
                 f"type=local,dest={destination}", str(context)],
                input=b"FROM scratch\nCOPY . /\n", check=True,
            )
            actual = {str(p.relative_to(destination)) for p in destination.rglob("*") if p.is_file()}
            if actual != expected:
                raise AssertionError((ignore, "missing", expected - actual, "unexpected", actual - expected))
            print("PASS", ignore, flush=True)


if __name__ == "__main__":
    main()
