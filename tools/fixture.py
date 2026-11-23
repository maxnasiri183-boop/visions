"""Create the private, deterministic 2-second demo feed; requires ffmpeg on PATH."""

import secrets
import subprocess
from hashlib import sha256
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "private"


def download(name, url, digest):
    target = PRIVATE / name
    data = target.read_bytes() if target.exists() else urlopen(url, timeout=60).read()
    if sha256(data).hexdigest() != digest:
        raise ValueError(f"Checksum mismatch: {name}")
    target.write_bytes(data)


def main():
    PRIVATE.mkdir(mode=0o700, exist_ok=True)
    try:
        with (ROOT / ".env").open("x") as env:
            env.write("S3_PASSWORD=" + secrets.token_hex(24) + "\n")
        (ROOT / ".env").chmod(0o600)
    except FileExistsError:
        pass
    download(
        "yunet.onnx",
        "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
        "face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    )
    download(
        "astronaut.png",
        "https://raw.githubusercontent.com/scikit-image/scikit-image/v0.24.0/skimage/data/astronaut.png",
        "88431cd9653ccd539741b555fb0a46b61558b301d4110412b5bc28b5e3ea6cb5",
    )
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-loop",
            "1",
            "-framerate",
            "10",
            "-i",
            str(PRIVATE / "astronaut.png"),
            "-vf",
            "drawbox=enable='gte(t,1)':color=black:t=fill",
            "-frames:v",
            "20",
            "-c:v",
            "ffv1",
            "-threads",
            "1",
            "-pix_fmt",
            "bgr0",
            "-fflags",
            "+bitexact",
            "-flags:v",
            "+bitexact",
            str(PRIVATE / "feed.mkv"),
        ],
        check=True,
    )
    print("feed.mkv | 10 fps | frames 0..9 face, 10..19 empty | default selection 5")
    print("clip_sha256", sha256((PRIVATE / "feed.mkv").read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
