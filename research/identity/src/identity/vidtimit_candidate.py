"""Prepare immutable manifests, real-time clips and an isolated SFace gallery candidate."""

import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import subprocess
from zipfile import ZipFile

from .corpus import SUBJECTS, archive_frames
from .import_vidtimit import RESERVE, digest

PREFIX = Path("research/vidtimit43")
SIZES = (8, 16, 28)


def frozen(path, value):
    encoded = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    if path.exists():
        if path.read_bytes() != encoded:
            raise ValueError(f"Frozen candidate record conflicts: {path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(encoded)
    tmp.replace(path)


def aliases(root, people):
    # A private random key prevents lookup by enumerating the publisher's 43 codes.
    path = root / ".alias-key"
    if not path.exists():
        with path.open("xb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(os.urandom(32))
    key = path.read_bytes()
    if len(key) != 32:
        raise ValueError("Expected a 32-byte private gallery alias key")

    def token(kind, subject):
        return hmac.digest(key, (kind + "|" + subject).encode(), "sha256").hex()[:24]

    people = sorted(people, key=lambda p: token("ordering", p))
    result = {
        p: {
            "person_id": "p-" + token("person", p),
            "display_name": f"Participant {i:03d}",
            "enrollment_session": "enr-" + token("session1", p),
            "video_session": "vt-" + token("session2", p),
        }
        for i, p in enumerate(people, 1)
    }
    manifest = root / "private-alias-crosswalk.json"
    frozen(manifest, result)
    manifest.chmod(0o600)
    return result


def partition(people):
    def order(subject):
        return hashlib.sha256(("vidtimit43-v1|" + subject).encode()).hexdigest()

    fresh = sorted(set(people) - SUBJECTS.keys(), key=order)
    if len(fresh) != 33 or len(people) != 43:
        raise ValueError("Expected the full 43-person release")
    result = {}
    for p, (kind, _) in SUBJECTS.items():
        result[p] = {
            "enrolled": kind == "enrolled",
            "in_video": kind != "absent",
            "split": "calibration",
        }
    # Freeze subjects before extracting/scoring images. Previous pilot participants
    # contribute to calibration only. Every evaluation probe belongs to a new person.
    for i, p in enumerate(fresh):
        result[p] = {
            "enrolled": i < 25,
            "in_video": i < 29,
            "split": "calibration" if i < 7 else "evaluation",
        }
    return result


def original_frames(archive, subject, take):
    # Most members are zero-padded to three digits. Two head sequences extend
    # beyond 999, where lexicographic sorting would mix later and earlier PTS.
    names = sorted(
        archive_frames(archive, subject, take), key=lambda n: int(n.rsplit("/", 1)[-1])
    )
    if [int(n.rsplit("/", 1)[-1]) for n in names] != list(range(1, len(names) + 1)):
        raise ValueError("Unexpected gap in numbered source images")
    return names


def photograph(root, z, subject, take, role, index=7):
    names = original_frames(z, subject, take)
    if len(names) <= index:
        raise ValueError(f"Missing original frame in {take}")
    member = names[index]
    raw = z.read(member)
    relative = PREFIX / role / (subject + ".jpg")
    dest = root / relative
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.read_bytes() != raw:
        raise ValueError("Candidate photograph differs from its original member")
    if not dest.exists():
        temp = dest.with_suffix(".part")
        temp.write_bytes(raw)
        temp.replace(dest)
    return {
        "person_id": subject,
        "path": str(relative),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "archive": subject + ".zip",
        "member": member,
        "session_id": subject + (":session1" if role == "enrollment" else ":session3"),
    }


def reference_photo(root, archive, subject):
    from . import core as c

    # Use the first eligible session-1 reference in this fixed order. Only enrollment
    # quality gates participate; no calibration, video or evaluation score is read.
    for index in (7, 15, 31):
        for take in ("sa1", "sa2"):
            names = original_frames(archive, subject, take)
            if len(names) <= index:
                continue
            image = c.upload_image(archive.read(names[index]))
            faces = c.detect(image)
            if len(faces) == 1 and c.features(image, faces[0])[0] is not None:
                return photograph(root, archive, subject, take, "enrollment", index)
    raise ValueError(f"No eligible session-1 reference for {subject}")


def clip(root, z, subject, take):
    names = original_frames(z, subject, take)
    if not names:
        raise ValueError(f"Missing session-2 sequence {take}")
    relative = PREFIX / "stream" / f"v43-{subject}-{take}.mkv"
    path = root / relative
    receipt = path.with_suffix(".json")
    if receipt.exists():
        saved = json.loads(receipt.read_text())
        if not path.is_file() or digest(path) != saved["sha256"]:
            raise ValueError("Candidate clip checksum differs from its receipt")
        return saved
    path.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root).free < RESERVE + 512 * 1024**2:
        raise RuntimeError("Disk reserve reached before media conversion")
    tmp = path.with_suffix(".part")
    # Original JPEG bytes become individual Matroska packets at the documented 25 Hz.
    # A packet-level digest check below covers every original frame in sequence.
    expected = []
    command = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-y",
        "-f",
        "mjpeg",
        "-framerate",
        "25",
        "-i",
        "pipe:0",
        "-map",
        "0:v:0",
        "-c:v",
        "copy",
        "-map_metadata",
        "-1",
        "-fflags",
        "+bitexact",
        "-f",
        "matroska",
        str(tmp),
    ]
    with subprocess.Popen(command, stdin=subprocess.PIPE) as encoder:
        try:
            for name in names:
                raw = z.read(name)
                encoder.stdin.write(raw)
                expected.append(hashlib.sha256(raw).hexdigest())
        finally:
            encoder.stdin.close()
        if encoder.wait():
            raise RuntimeError("Could not remux source JPEG sequence")
    probe = json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_packets",
                "-show_streams",
                "-show_data_hash",
                "sha256",
                "-show_entries",
                "packet=pts_time,data_hash:stream=width,height,codec_name",
                "-of",
                "json",
                str(tmp),
            ]
        )
    )
    packets = probe["packets"]
    if [p["data_hash"].split(":")[-1].lower() for p in packets] != expected or any(
        abs(float(packet["pts_time"]) - i / 25) > 0.001
        for i, packet in enumerate(packets)
    ):
        raise ValueError("Source frame hash or PTS changed during remux")
    tmp.replace(path)
    entry = {
        "person_id": subject,
        "take": take,
        "session_id": subject + ":session2",
        "archive": subject + ".zip",
        "path": str(relative),
        "sha256": digest(path),
        "frames": len(packets),
        "fps": 25,
        "first_member": names[0],
        "last_member": names[-1],
        "width": probe["streams"][0]["width"],
        "height": probe["streams"][0]["height"],
        "verified_packet_hashes": len(expected),
        "mux_profile": "bitexact-matroska-mjpeg-v1",
    }
    frozen(receipt, entry)
    return entry


def prepare(root, pilot, ledger):
    from . import core as c

    if c.ROOT != root:
        raise ValueError("Recognition root must point at the private candidate")
    (root / "models").mkdir(exist_ok=True)
    model = root / "models/sface.onnx"
    if not model.exists():
        try:
            os.link(pilot / "models/sface.onnx", model)
        except OSError:
            temporary = model.with_suffix(".part")
            shutil.copyfile(pilot / "models/sface.onnx", temporary)
            temporary.replace(model)
    c.engines()
    people = [row["subject"] for row in ledger]
    roles = partition(people)
    crosswalk = aliases(root, people)
    frozen(root / "split-plan.json", roles)
    records = {
        role: [] for role in ("enrollment", "calibration", "evaluation", "stream")
    }
    for subject in sorted(people):
        assignment = roles[subject]
        with ZipFile(root / "archives" / (subject + ".zip")) as archive:
            if assignment["enrolled"]:
                photo = reference_photo(root, archive, subject)
                result = c.add_reference(
                    (root / photo["path"]).read_bytes(),
                    crosswalk[subject]["person_id"],
                    crosswalk[subject]["display_name"],
                    crosswalk[subject]["enrollment_session"],
                )
                if result["status"] != "accepted":
                    raise ValueError(
                        f"Fixed reference failed product quality check for {subject}: {result['reason']}"
                    )
                records["enrollment"].append(photo)
            phase = assignment["split"]
            records[phase].append(photograph(root, archive, subject, "head3", phase))
            if assignment["in_video"]:
                takes = sorted(
                    {
                        name.split("/")[2]
                        for name in archive.namelist()
                        if name.startswith(subject + "/video/")
                        and len(name.split("/")) >= 4
                        and not name.split("/")[2].startswith("head")
                    }
                )
                if len(takes) != 10:
                    raise ValueError(
                        "Expected ten source speech takes for a participant"
                    )
                for take in ["head2", *takes[6:8]]:
                    records["stream"].append(clip(root, archive, subject, take))
    for phase, rows in records.items():
        frozen(root / (phase + ".json"), rows)
    catalog = [
        {
            "source_id": "studio",
            "asset_id": "vt-" + item["sha256"][:24],
            "session_id": crosswalk[item["person_id"]]["video_session"],
            "path": str(PREFIX / "media" / (item["sha256"] + ".mkv")),
            "sha256": item["sha256"],
            "synthetic": False,
        }
        for item in records["stream"]
    ]
    frozen(root / "catalog.candidate.json", catalog)
    return roles, records
