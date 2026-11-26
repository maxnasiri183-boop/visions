"""Check random-access evidence against an independent sequential video decode."""

import argparse
from contextlib import closing
from pathlib import Path

import cv2
import numpy as np

from identity.worker import Decoder


def check(path: Path):
    reference = cv2.VideoCapture(str(path))
    try:
        frames = [reference.read()[1] for _ in range(6)]
    finally:
        reference.release()
    assert all(frame is not None for frame in frames), "Need six decodable frames"
    with closing(Decoder(path.parent)) as decoder:
        for occurrence, index in [("A", 5), ("B", 0), ("A", 3), ("A", 3), ("B", 1)]:
            assert np.array_equal(decoder.frame(occurrence, path.name, index), frames[index])
        for name, index, error in [(path.name, 10**6, ValueError), ("../", 0, FileNotFoundError)]:
            try:
                decoder.frame("A", name, index)
            except error:
                pass
            else:
                raise AssertionError("Unavailable evidence was returned")
        assert np.array_equal(decoder.frame("A", path.name, 2), frames[2])
    assert not decoder.clips


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("clip", type=Path)
    check(parser.parse_args().clip.resolve())
    print("PASS retained pixels, replay isolation, retry and release")
