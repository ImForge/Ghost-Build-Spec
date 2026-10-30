"""The 30-second evidence ring buffer.

These frames are NOT pixelated — this is the evidence. They exist only in RAM and
only ever leave the node encrypted, after an explicit seal command (see
node/escrow_client.py and the invariant in CLAUDE.md). Nothing in this module
writes to disk or to the network.
"""
from __future__ import annotations

import io
import logging
import threading
import time
import zipfile
from collections import deque
from typing import Any

import cv2
import numpy as np

RING_QUALITY = 70
RING_WIDTH = 640


class RingBuffer:
    """Recent frames as JPEG bytes, oldest dropped automatically."""

    def __init__(self, cam: str, seconds: int = 30, fps: float = 5.0) -> None:
        self.cam = cam
        self.seconds = seconds
        self.fps = fps
        self.log = logging.getLogger(f"node.ringbuf {cam}")
        self._interval = 1.0 / fps if fps > 0 else 0.0
        self._last = 0.0
        self._lock = threading.Lock()
        self._frames: deque[tuple[float, bytes]] = deque(maxlen=max(1, int(seconds * fps)))

    def __len__(self) -> int:
        with self._lock:
            return len(self._frames)

    def maybe_add(self, frame_bgr: np.ndarray, ts: float | None = None) -> bool:
        """Add a frame if enough time has passed. Returns True if it was stored."""
        now = time.time() if ts is None else ts
        if self._interval:
            due = self._last + self._interval
            # The epsilon matters: timestamps arrive as floats, and a strict
            # comparison drops roughly one frame in five to accumulated error,
            # which would make a sealed clip shorter than its meta.fps claims.
            if now < due - 1e-6:
                return False
            # Keep a steady cadence instead of drifting with jitter, but do not
            # try to backfill after a long gap (a stalled or reopened source).
            self._last = due if now - due < self._interval else now

        img = frame_bgr
        h, w = img.shape[:2]
        if w > RING_WIDTH:
            scale = RING_WIDTH / float(w)
            img = cv2.resize(img, (RING_WIDTH, max(1, int(round(h * scale)))),
                             interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), RING_QUALITY])
        if not ok:
            self.log.warning("jpeg encode failed, frame dropped")
            return False
        with self._lock:
            self._frames.append((now, buf.tobytes()))
        return True

    def snapshot(self) -> list[tuple[float, bytes]]:
        with self._lock:
            return list(self._frames)

    def clear(self) -> None:
        with self._lock:
            self._frames.clear()

    def to_zip(self, evidence_id: str) -> tuple[bytes, dict[str, Any]]:
        """Pack the buffer into a ZIP of JPEGs plus meta.json.

        A ZIP rather than an mp4 on purpose: OpenCV's mp4v does not play in
        browsers and these wheels carry no dependable H.264 encoder. See
        docs/DECISIONS.md D1.
        """
        frames = self.snapshot()
        meta = {
            "cam": self.cam,
            "evidence_id": evidence_id,
            "fps": self.fps,
            "started_ts": frames[0][0] if frames else None,
            "ended_ts": frames[-1][0] if frames else None,
            "n_frames": len(frames),
        }
        import json

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("meta.json", json.dumps(meta, indent=2))
            for i, (_ts, jpg) in enumerate(frames, start=1):
                zf.writestr(f"frames/{i:06d}.jpg", jpg)
        self.log.info("packed %d frames for %s", len(frames), evidence_id)
        return buf.getvalue(), meta
