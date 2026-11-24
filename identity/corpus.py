"""Prepare the private, research-only VidTIMIT pilot from the creator's subject ZIPs."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from urllib.request import urlopen
from zipfile import ZipFile

import cv2
import numpy as np

# Different people provide independent trial units; the third capture session of
# each subject belongs to exactly one probe role. Head2 and head3 are named sessions.
SUBJECTS = {
    "faks0": ("enrolled", "upload"),
    "fadg0": ("present", "upload"),
    "mabw0": ("enrolled", "calibration"),
    "fcft0": ("enrolled", "evaluation"),
    "mbdg0": ("present", "evaluation"),
    "mbjk0": ("absent", "calibration"),
    "fcmh0": ("absent", "evaluation"),
    "fcmr0": ("absent", "upload"),
    "mccs0": ("absent", "evaluation"),
    "mcem0": ("absent", "calibration"),
}
CREDIT = (
    "VidTIMIT, Conrad Sanderson. C. Sanderson and B.C. Lovell, Multi-Region Probabilistic "
    "Histograms for Robust and Scalable Identity Inference, LNCS 5558, 199–208, 2009."
)
NOAA = "https://cdn.star.nesdis.noaa.gov/GOES19/ABI/FD/GEOCOLOR/"


def record(root, path, sessions, people, frame_rate=None, **extra):
    return dict(
        path=str(path.relative_to(root)),
        sha256=sha256(path.read_bytes()).hexdigest(),
        sessions=sessions,
        people=people,
        frame_rate=frame_rate,
        **extra,
    )


def archive_frames(z, person, sequence):
    prefix = f"{person}/video/{sequence}/"
    return sorted(
        n for n in z.namelist() if n.startswith(prefix) and not n.endswith("/")
    )


def get_image(z, path):
    return cv2.imdecode(np.frombuffer(z.read(path), np.uint8), cv2.IMREAD_COLOR)


def encode(path, frames, fps):
    height, width = frames[0].shape[:2]
    proc = subprocess.Popen(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-s",
            f"{width}x{height}",
            "-r",
            str(fps),
            "-i",
            "pipe:0",
            "-c:v",
            "ffv1",
            "-threads",
            "1",
            "-f",
            "matroska",
            str(path.with_suffix(".tmp")),
        ],
        stdin=subprocess.PIPE,
    )
    for frame in frames:
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    if proc.wait():
        raise RuntimeError("Clip encoding failed")
    path.with_suffix(".tmp").replace(path)


def dump(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


def build(root, partial=False):
    base = root / "research"
    for name in (
        "archives",
        "stream",
        "enrollment",
        "upload",
        "evaluation",
        "calibration",
    ):
        (base / name).mkdir(parents=True, exist_ok=True)
    members = {
        k: [] for k in ("enrollment", "stream", "upload", "calibration", "evaluation")
    }
    motion = {}
    for person, (kind, split) in SUBJECTS.items():
        archive = base / "archives" / (person + ".zip")
        if not archive.exists() and partial:
            continue
        with ZipFile(archive) as z:
            roles = [(split, "head3", 3)]
            if kind == "enrolled":
                roles.append(("enrollment", "sa1", 1))
            for role, seq, session in roles:
                names = archive_frames(z, person, seq)
                n = names[min(7, len(names) - 1)]
                path = base / role / (person + ".jpg")
                path.write_bytes(z.read(n))
                members[role].append(
                    record(
                        root,
                        path,
                        [f"{person}:session{session}"],
                        [person],
                        member=n,
                        archive=archive.name,
                        gallery_label=kind,
                    )
                )
            if kind != "absent":
                names = archive_frames(z, person, "head2")
                # Capture at 25 Hz. Select every 2nd original frame, preserving real-time pace.
                motion[person] = [get_image(z, n) for n in names[:100:2]]
    catalog = []

    def clip(asset, people, frames, fps, composed=False, source="studio"):
        path = base / "stream" / (asset + ".mkv")
        if not path.exists():
            encode(path, frames, fps)
        entry = record(
            root,
            path,
            [f"{p}:session2" for p in people] or ["goes19:2026-328"],
            people,
            fps,
            composed=composed,
            frames=len(frames),
        )
        members["stream"].append(entry)
        catalog.append(
            dict(
                source_id=source,
                asset_id=asset,
                session_id="-".join(entry["sessions"]).replace(":", "-"),
                path=entry["path"],
                sha256=entry["sha256"],
                synthetic=composed,
            )
        )

    for person, frames in motion.items():
        clip("solo-" + person, [person], frames, 12.5)
    if all(person in motion for person in ("faks0", "fadg0")):
        # Spatial composition uses simultaneously presented, separately captured moving sequences.
        clip(
            "duo",
            ["faks0", "fadg0"],
            [np.hstack(pair) for pair in zip(motion["faks0"], motion["fadg0"])],
            12.5,
            composed=True,
            source="studio",
        )
    if "faks0" in motion:
        original = motion["faks0"]
        h, w = original[0].shape[:2]
        modified = []
        for group in ("small", "blur", "dim"):
            for frame in original[:16]:
                if group == "small":
                    canvas = np.zeros_like(frame)
                    canvas[: h // 5, : w // 5] = cv2.resize(frame, (w // 5, h // 5))
                    modified.append(canvas)
                elif group == "blur":
                    modified.append(cv2.GaussianBlur(frame, (0, 0), 6))
                else:
                    modified.append(cv2.convertScaleAbs(frame, alpha=0.14))
        clip("quality-stress", ["faks0"], modified, 12.5, composed=True)
        members["stream"][-1]["conditions"] = {
            "small": [0, 15],
            "blur": [16, 31],
            "dim": [32, 47],
        }
    satellite = []
    for t in ("0040", "0050", "0100", "0110", "0120", "0130", "0140", "0150"):
        basename = "2026328" + t + "_GOES19-ABI-FD-GEOCOLOR-678x678.jpg"
        path = base / "archives" / basename
        if not path.exists():
            with urlopen(NOAA + basename, timeout=30) as res:
                path.write_bytes(res.read())
        satellite.append(cv2.imread(str(path)))
    clip("empty", [], satellite, 2.0)
    for role, entries in members.items():
        dump(base / (role + ".json"), entries)
    dump(root / "catalog.json", catalog)
    (base / "CREDITS.txt").write_text(
        CREDIT + "\nhttps://www.conradsanderson.id.au/vidtimit/\n"
        "Original license notice is retained in archives/VidTIMIT-original-notice.html. Private research pilot.\n"
        "Empty scene: CIRA/NOAA, NOAA/NESDIS Center for Satellite Applications and Research. "
        "GOES-19 ABI GeoColor. Capture samples 10 minutes apart played at 2 FPS.\n"
        + NOAA
        + "\n"
    )
    print(
        f"{len(catalog)} clips, splits: " + str({k: len(v) for k, v in members.items()})
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("private/operator"))
    parser.add_argument(
        "--partial",
        action="store_true",
        help="Include downloaded subject archives only",
    )
    args = parser.parse_args()
    build(args.root, args.partial)
