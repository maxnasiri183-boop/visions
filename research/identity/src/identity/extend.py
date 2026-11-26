"""Append held-out session-2 speech takes without changing the frozen calibration corpus."""

from zipfile import ZipFile

from . import core as c
from .corpus import SUBJECTS, append_playlist, archive_frames, dump, encode, get_image, record


def extend():
    base = c.ROOT / "research"
    path = c.ROOT / "catalog.json"
    catalog = []
    additions = []
    for person, (kind, _) in SUBJECTS.items():
        if kind == "absent":
            continue
        with ZipFile(base / "archives" / (person + ".zip")) as archive:
            # VidTIMIT documentation p.1 assigns sentences 7 and 8 in lexicographic order to session 2.
            takes = sorted(
                {
                    n.split("/")[2]
                    for n in archive.namelist()
                    if n.startswith(person + "/video/s") and len(n.split("/")) >= 4
                }
            )
            if len(takes) != 10:
                raise ValueError("Unexpected VidTIMIT sentence count")
            for take in takes[6:8]:
                asset = "speech-" + person + "-" + take
                clip = base / "stream" / (asset + ".mkv")
                members = archive_frames(archive, person, take)[:100:2]
                if not clip.exists():
                    encode(clip, [get_image(archive, n) for n in members], 12.5)
                row = record(
                    c.ROOT,
                    clip,
                    [person + ":session2"],
                    [person],
                    12.5,
                    composed=False,
                    frames=len(members),
                    take=take,
                    archive=person + ".zip",
                )
                additions.append(row)
                catalog.append(
                    dict(
                        source_id="studio",
                        asset_id=asset,
                        session_id=person + "-session2",
                        path=row["path"],
                        sha256=row["sha256"],
                        synthetic=False,
                    )
                )
    append_playlist(path, catalog)
    dump(base / "stream-extension.json", additions)
    print(f"Added {len(additions)} distinct capture assets to the playlist")


if __name__ == "__main__":
    extend()
