"""Enroll the private pilot and create reproducible examples for operator acceptance."""

from zipfile import ZipFile
import cv2
import numpy as np
from . import core as c
from .corpus import dump, record


def enroll():
    base = c.ROOT / "research"
    for path in sorted((base / "enrollment").glob("*.jpg")):
        c.add_reference(
            path.read_bytes(),
            path.stem,
            "Participant " + path.stem.upper(),
            path.stem + ":session1",
        )
    # Negative enrollment check from a separate frame of the same enrollment capture.
    # Severe deliberate downsampling makes this a quality-control example.
    with ZipFile(base / "archives/faks0.zip") as archive:
        raw = archive.read("faks0/video/sa1/012")
        img = c.upload_image(raw)
    small = cv2.resize(img, (32, 24), interpolation=cv2.INTER_AREA)
    _, jpeg = cv2.imencode(".jpg", small)
    rejected = c.add_reference(
        jpeg.tobytes(), "faks0", "Participant FAKS0", "faks0:session1"
    )
    if rejected["status"] != "rejected":
        raise ValueError("Negative enrollment example unexpectedly passed")
    # This picker example combines two existing uploaded probes of session 3.
    # It contributes no additional independent trial to the five frozen manifests.
    directory = base / "examples"
    directory.mkdir(exist_ok=True)
    a, b = [
        cv2.imread(str(base / "upload" / (name + ".jpg")))
        for name in ("faks0", "fadg0")
    ]
    path = directory / "multi-probe.jpg"
    cv2.imwrite(str(path), np.hstack((a, b)))
    dump(
        directory / "examples.json",
        [
            record(
                c.ROOT,
                path,
                ["faks0:session3", "fadg0:session3"],
                ["faks0", "fadg0"],
                composed=True,
                purpose="Face picker only, derived from independent upload probes; excluded from metric denominators",
            )
        ],
    )
    if not c.gallery()["versions"]:
        c.publish()


if __name__ == "__main__":
    enroll()
