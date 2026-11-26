"""Read exact decoded frames from the local retained-video archive."""

from collections import OrderedDict
from pathlib import Path

import cv2
import numpy as np

from . import core


class Decoder:
    """One reader per worker or request. Replayed occurrences have separate cursors."""

    def __init__(self, root: Path | None = None):
        self.root = (root if root is not None else core.ROOT).resolve()
        self.clips: OrderedDict[
            tuple[str, Path], tuple[cv2.VideoCapture, int, np.ndarray | None]
        ] = OrderedDict()

    def close(self) -> None:
        while self.clips:
            self.clips.popitem()[1][0].release()

    def frame(self, occurrence_id: str, path: str, index: int) -> np.ndarray:
        retained = (self.root / path).resolve()
        if not retained.is_relative_to(self.root) or not retained.is_file():
            raise FileNotFoundError("Retained source is unavailable")
        if index < 0:
            raise ValueError("Invalid source frame")
        key = (occurrence_id, retained)
        if key not in self.clips:
            if len(self.clips) == 8:
                self.clips.popitem(last=False)[1][0].release()
            cap = cv2.VideoCapture(
                str(retained), cv2.CAP_FFMPEG, [cv2.CAP_PROP_N_THREADS, 1]
            )
            self.clips[key] = (cap, -1, None)
        self.clips.move_to_end(key)
        cap, before, image = self.clips[key]
        if before == index and image is not None:
            return image
        try:
            if before + 1 != index and not cap.set(cv2.CAP_PROP_POS_FRAMES, index):
                raise ValueError("Cannot seek source frame")
            ok, image = cap.read()
            if not ok:
                raise ValueError("Could not decode retained source")
        except (ValueError, cv2.error):
            self.clips.pop(key)[0].release()
            raise
        self.clips[key] = (cap, index, image)
        return image
