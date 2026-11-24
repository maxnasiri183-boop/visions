"""Download pinned models and, on request, the private research pilot subset."""

import argparse
from hashlib import md5, sha256
import json
import shutil
from urllib.request import urlopen

from . import core as c
from .corpus import SUBJECTS, build

SFACE = (
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/"
    "48b10fa505aeb51fbd127fc60a5c660040e823b7/models/face_recognition_sface/"
    "face_recognition_sface_2021dec.onnx"
)


def download(url, path, digest=None, algorithm=sha256):
    if path.is_file() and (
        not digest or algorithm(path.read_bytes()).hexdigest() == digest
    ):
        return
    tmp = path.with_suffix(".part")
    with urlopen(url, timeout=120) as src, tmp.open("wb") as dst:
        shutil.copyfileobj(src, dst)
    if digest and algorithm(tmp.read_bytes()).hexdigest() != digest:
        raise ValueError("Download checksum differs for " + path.name)
    tmp.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-vidtimit", action="store_true")
    args = parser.parse_args()
    modeldir = c.ROOT / "models"
    modeldir.mkdir(parents=True, mode=0o700, exist_ok=True)
    download(SFACE, modeldir / "sface.onnx", c.MODEL_SHA)
    if args.research_vidtimit:
        base = c.ROOT / "research/archives"
        base.mkdir(parents=True, mode=0o700, exist_ok=True)
        download(
            "https://www.conradsanderson.id.au/vidtimit/",
            base / "VidTIMIT-original-notice.html",
        )
        download(
            "https://www.conradsanderson.id.au/vidtimit/zips/vidtimit_documentation.pdf",
            base / "vidtimit_documentation.pdf",
        )
        with urlopen("https://zenodo.org/api/records/158963", timeout=45) as res:
            metadata = json.load(res)
        sums = {f["key"]: f["checksum"].removeprefix("md5:") for f in metadata["files"]}
        for person in SUBJECTS:
            name = person + ".zip"
            download(
                "https://zenodo.org/records/158963/files/" + name + "?download=1",
                base / name,
                sums[name],
                md5,
            )
        build(c.ROOT)


if __name__ == "__main__":
    main()
