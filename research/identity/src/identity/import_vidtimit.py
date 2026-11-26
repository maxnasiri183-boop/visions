"""Import the 43-subject VidTIMIT release into an isolated private research root."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .corpus import CREDIT, dump

RECORD = "https://zenodo.org/api/records/158963"
RELEASE = "https://zenodo.org/records/158963/files/"
PUBLISHER = "https://conradsanderson.id.au/vidtimit/"
RESERVE = 24 * 1024**3


def digest(path, algorithm="sha256"):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()


def acquire(url, output, size, md5):
    partial = output.with_suffix(output.suffix + ".part")
    for attempt in range(7):
        offset = partial.stat().st_size if partial.exists() else 0
        if offset == size and digest(partial, "md5") == md5:
            partial.replace(output)
            return
        if offset >= size:
            partial.unlink()
            offset = 0
        request = Request(url, headers={"Range": f"bytes={offset}-"} if offset else {})
        try:
            with urlopen(request, timeout=65) as response:
                append = response.status == 206
                if append and not response.headers.get("Content-Range", "").startswith(
                    f"bytes {offset}-"
                ):
                    raise ValueError("Unexpected HTTP range in release download")
                with partial.open("ab" if append else "wb") as stream:
                    shutil.copyfileobj(response, stream, 1024**2)
            if partial.stat().st_size == size and digest(partial, "md5") == md5:
                partial.replace(output)
                return
            if partial.stat().st_size >= size:
                partial.unlink()
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            if attempt == 6:
                raise
            wait = getattr(exc, "headers", None)
            delay = wait.get("Retry-After", "") if wait else ""
            time.sleep(min(90, int(delay)) if delay.isdigit() else min(30, 2**attempt))
    raise ValueError(f"Release checksum mismatch for {output.name}")


def acquire_archives(root, pilot):
    release = root / "archives"
    release.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata_path = root / "zenodo-record.json"
    if not metadata_path.exists():
        with urlopen(RECORD, timeout=45) as response:
            dump(metadata_path, json.load(response))
    metadata = json.loads(metadata_path.read_text())
    entries = {
        f["key"]: f
        for f in metadata["files"]
        if re.fullmatch(r"[fm][a-z]{3}\d\.zip", f["key"])
    }
    if int(metadata["id"]) != 158963 or len(entries) != 43:
        raise ValueError(
            "Expected 43 VidTIMIT subject archives from Zenodo record 158963"
        )
    source = pilot / "research/archives"
    for name, url in {
        "VidTIMIT-original-notice.html": PUBLISHER,
        "vidtimit_documentation.pdf": PUBLISHER + "zips/vidtimit_documentation.pdf",
    }.items():
        target = release / name
        if not target.exists():
            if (source / name).is_file():
                shutil.copyfile(source / name, target)
            else:
                with urlopen(url, timeout=45) as response:
                    target.write_bytes(response.read())
    (release / "CREDITS.txt").write_text(
        CREDIT + "\n" + PUBLISHER + "\n" + RECORD + "\n"
    )
    ledger = []
    for i, (name, item) in enumerate(sorted(entries.items()), 1):
        size = int(item["size"])
        md5 = item["checksum"].removeprefix("md5:")
        if not re.fullmatch("[0-9a-f]{32}", md5):
            raise ValueError("Invalid publisher checksum")
        file = release / name
        if file.exists():
            if file.stat().st_size != size or digest(file, "md5") != md5:
                raise ValueError(
                    f"Existing archive differs from publisher release: {name}"
                )
        else:
            if shutil.disk_usage(root).free < RESERVE + size + 512 * 1024**2:
                raise RuntimeError(
                    "Insufficient disk reserve to import the next subject"
                )
            previous = source / name
            if (
                previous.is_file()
                and previous.stat().st_size == size
                and digest(previous, "md5") == md5
            ):
                try:
                    os.link(previous, file)
                except OSError:
                    shutil.copyfile(previous, file)
            else:
                acquire(RELEASE + name + "?download=1", file, size, md5)
        ledger.append(
            {
                "subject": file.stem,
                "archive": name,
                "bytes": size,
                "publisher_md5": md5,
                "sha256": digest(file),
            }
        )
        dump(root / "archive-ledger.json", ledger)
        print(f"Verified publisher archive {i}/{len(entries)}", flush=True)
    return ledger


def checked_ledger(root):
    entries = json.loads((root / "archive-ledger.json").read_text())
    release = json.loads((root / "zenodo-record.json").read_text())
    expected = {
        f["key"]: f
        for f in release["files"]
        if re.fullmatch(r"[fm][a-z]{3}\d\.zip", f["key"])
    }
    if (
        len(entries) != 43
        or len(expected) != 43
        or {r["archive"] for r in entries} != set(expected)
    ):
        raise ValueError(
            "The 43 verified archives must be complete before preparing the candidate"
        )
    for row in entries:
        name = row["archive"]
        source = expected[name]
        path = root / "archives" / name
        if (
            row["subject"] != Path(name).stem
            or path.stat().st_size != source["size"]
            or row["publisher_md5"] != source["checksum"].removeprefix("md5:")
            or digest(path) != row["sha256"]
        ):
            raise ValueError(f"Stored release ledger differs for {name}")
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, required=True, help="Separate private candidate directory"
    )
    parser.add_argument(
        "--pilot-root",
        type=Path,
        required=True,
        help="Read-only source of existing archives and pinned models",
    )
    parser.add_argument(
        "--stage", choices=("all", "archives", "candidate"), default="all"
    )
    args = parser.parse_args()
    root, pilot = args.root.resolve(), args.pilot_root.resolve()
    if root == pilot or root.is_relative_to(pilot) or pilot.is_relative_to(root):
        parser.error("Candidate and live data directories must be separate")
    os.umask(0o077)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (root / ".import.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ledger = (
            acquire_archives(root, pilot)
            if args.stage != "candidate"
            else checked_ledger(root)
        )
        if args.stage != "archives":
            os.environ["OPERATOR_ROOT"] = str(root)
            os.environ.setdefault("IDENTITY_YUNET", str(pilot.parent / "yunet.onnx"))
            from .vidtimit_candidate import prepare
            from .vidtimit_validation import evaluate

            roles, records = prepare(root, pilot, ledger)
            evaluate(root, roles, records)


if __name__ == "__main__":
    main()
