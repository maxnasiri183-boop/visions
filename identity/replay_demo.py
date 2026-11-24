"""Build a private, deterministic replay fixture at the native 25 Hz subject cadence."""
import argparse
from pathlib import Path
from zipfile import ZipFile

import cv2
import numpy as np

from .corpus import append_playlist, archive_frames, dump, encode, get_image, record


def prepare(root):
    base = root / "research"
    frames = []
    provenance = []
    for person in ("faks0", "fadg0"):
        with ZipFile(base / "archives" / (person + ".zip")) as z:
            members = archive_frames(z, person, "head2")[100:125]
            if len(members) != 25:
                raise ValueError("Independent session-2 capture is incomplete")
            frames.append([get_image(z, member) for member in members])
            provenance.append(dict(session=person + ":session2", members=members))
    pair = [np.hstack(frame) for frame in zip(*frames)]
    # Timelapse interlude shows actual licensed empty-scene captures. It does not add unique subject time.
    satellites = sorted((base / "archives").glob("*678x678.jpg"))
    if len(satellites) != 8:
        raise ValueError("Prepare the eight attributed NOAA empty frames first")
    h, w = pair[0].shape[:2]
    empty = [cv2.resize(cv2.imread(str(p)), (w,h)) for p in satellites]
    frames = pair + [empty[min(i*len(empty)//125, len(empty)-1)] for i in range(125)]
    path = base / "stream" / "paired-passage.mkv"
    if not path.exists():
        encode(path, frames, 25)
    meta = record(root, path, ["faks0:session2","fadg0:session2"], ["faks0","fadg0"], 25,
                  captures=provenance, empty_frames=[25,149], empty_sources=[p.name for p in satellites],
                  frames=len(frames), expected_faces=[2]*25+[0]*125)
    dump(base / "paired-passage.json", meta)
    append_playlist(root / "catalog.json", [dict(source_id="replay-lab", asset_id="paired-passage",
        session_id="vidtimit-session2-noaa", path=meta["path"], sha256=meta["sha256"], synthetic=True)])
    print("replay-lab: 150 frames, 6 s, 25 FPS, 25 spatially composed moving frames then 125 empty frames")
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("private/operator"))
    prepare(parser.parse_args().root)
