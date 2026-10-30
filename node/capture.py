"""Frame capture and the L0 motion gate.

The motion gate is the first privacy and power win: an empty corridor costs one
inference per second instead of ten. It compares successive 160-px-wide greyscale
copies of the frame, which is cheap enough to run on every frame even on a Pi.
"""
from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass

import cv2
import numpy as np

CAPTURE_WIDTH = 640
CAPTURE_HEIGHT = 480
MOTION_WIDTH = 160


@dataclass
class Frame:
    """One captured frame, already resized, plus the motion verdict for it."""

    bgr: np.ndarray
    ts: float
    motion: bool
    motion_score: float

    @property
    def size(self) -> tuple[int, int]:
        h, w = self.bgr.shape[:2]
        return w, h


class Capture:
    """A camera index, a video file or an RTSP URL, behind one interface."""

    def __init__(
        self,
        source: int | str,
        cam: str,
        *,
        loop: bool = False,
        motion_thresh: float = 4.0,
        width: int = CAPTURE_WIDTH,
    ) -> None:
        self.source = source
        self.cam = cam
        self.loop = loop
        self.motion_thresh = motion_thresh
        self.width = width
        self.log = logging.getLogger(f"node.capture {cam}")
        self._cap: cv2.VideoCapture | None = None
        self._prev_small: np.ndarray | None = None
        self._frames = 0

    @property
    def is_webcam(self) -> bool:
        return isinstance(self.source, int)

    @property
    def native_fps(self) -> float:
        """The source's own frame rate, or 0.0 if it does not report one.

        Used to pace file and RTSP playback: reading a file as fast as the disk
        allows would make the brain see minutes of activity per second.
        """
        if self._cap is None:
            return 0.0
        fps = float(self._cap.get(cv2.CAP_PROP_FPS) or 0.0)
        return fps if 0.0 < fps < 240.0 else 0.0

    def open(self) -> None:
        """Open the source. Raises RuntimeError if it cannot be read."""
        if self.is_webcam and sys.platform == "win32":
            # Windows' default MSMF backend hangs on some webcams; DSHOW does not.
            cap = cv2.VideoCapture(self.source, cv2.CAP_DSHOW)
        else:
            cap = cv2.VideoCapture(self.source)

        if self.is_webcam:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAPTURE_WIDTH)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAPTURE_HEIGHT)

        if not cap.isOpened():
            cap.release()
            raise RuntimeError(f"cannot open source {self.source!r}")

        self._cap = cap
        self.log.info("opened %r (%s)", self.source, "webcam" if self.is_webcam else "stream/file")

    def read(self) -> Frame | None:
        """Next frame, or None when a non-looping source has ended."""
        if self._cap is None:
            raise RuntimeError("read() before open()")

        ok, raw = self._cap.read()
        if not ok or raw is None:
            if self.loop and not self.is_webcam:
                self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, raw = self._cap.read()
                if not ok or raw is None:
                    self.log.warning("source ended and could not be rewound")
                    return None
            else:
                self.log.info("source ended after %d frames", self._frames)
                return None

        self._frames += 1
        bgr = self._resize(raw)
        score, motion = self._motion(bgr)
        return Frame(bgr=bgr, ts=time.time(), motion=motion, motion_score=score)

    def _resize(self, raw: np.ndarray) -> np.ndarray:
        """Everything downstream assumes this width, so do it before anything else."""
        h, w = raw.shape[:2]
        if w == self.width:
            return raw
        scale = self.width / float(w)
        return cv2.resize(raw, (self.width, max(1, int(round(h * scale)))),
                          interpolation=cv2.INTER_AREA)

    def _motion(self, bgr: np.ndarray) -> tuple[float, bool]:
        h, w = bgr.shape[:2]
        small_h = max(1, int(round(h * MOTION_WIDTH / float(w))))
        small = cv2.cvtColor(
            cv2.resize(bgr, (MOTION_WIDTH, small_h), interpolation=cv2.INTER_AREA),
            cv2.COLOR_BGR2GRAY,
        )
        if self._prev_small is None:
            self._prev_small = small
            # First frame has nothing to compare against; assume motion so the
            # node starts inferring immediately rather than idling for a second.
            return 0.0, True
        score = float(cv2.absdiff(self._prev_small, small).mean())
        self._prev_small = small
        return score, score >= self.motion_thresh

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
            self.log.info("closed after %d frames", self._frames)

    def __enter__(self) -> Capture:
        self.open()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
