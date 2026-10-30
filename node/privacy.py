"""Head pixelation.

A snapshot is the one still image a node may ever publish, and it leaves only
after heads have been destroyed in place. Pixelation is done by downscaling the
head ROI to 8x8 and scaling it back with nearest-neighbour, so the original pixels
are genuinely discarded rather than blurred over.
"""
from __future__ import annotations

import base64
import logging
from typing import Any, Sequence

import cv2
import numpy as np

SNAPSHOT_WIDTH = 320
SNAPSHOT_QUALITY = 60
MOSAIC = 8  # the ROI is reduced to this many pixels per side before being blown back up
MIN_RADIUS_PX = 20
HEAD_CONF = 0.3

# Keypoint indices used to locate a head, in order of preference.
KP_NOSE = 0
KP_FACE = (1, 2, 3, 4)  # eyes then ears
KP_SHOULDERS = (5, 6)

log = logging.getLogger("node.privacy")


def _kp(person: dict[str, Any], idx: int) -> tuple[float, float, float] | None:
    kps = person.get("kp") or []
    if idx >= len(kps):
        return None
    point = kps[idx]
    if len(point) < 3:
        return None
    return float(point[0]), float(point[1]), float(point[2])


def head_circle(person: dict[str, Any], w: int, h: int) -> tuple[int, int, int]:
    """Where a person's head is, in pixels: (cx, cy, radius).

    Nose first, then whatever eyes and ears are visible, then the top centre of
    the bounding box. The last case is what keeps a back-turned or badly lit
    person's face covered even when no facial keypoint survived.
    """
    bbox = person.get("bbox") or [0.0, 0.0, 0.0, 0.0]
    bx, by, bw, bh = (float(v) for v in bbox[:4])

    nose = _kp(person, KP_NOSE)
    if nose is not None and nose[2] >= HEAD_CONF:
        cx, cy = nose[0], nose[1]
    else:
        face = [p for i in KP_FACE if (p := _kp(person, i)) and p[2] >= HEAD_CONF]
        if face:
            cx = sum(p[0] for p in face) / len(face)
            cy = sum(p[1] for p in face) / len(face)
        else:
            cx, cy = bx + bw / 2.0, by + bh * 0.08

    left = _kp(person, KP_SHOULDERS[0])
    right = _kp(person, KP_SHOULDERS[1])
    if left and right and left[2] >= HEAD_CONF and right[2] >= HEAD_CONF:
        shoulder_px = float(np.hypot((left[0] - right[0]) * w, (left[1] - right[1]) * h))
    else:
        shoulder_px = 0.3 * bw * w

    radius = int(max(MIN_RADIUS_PX, 0.8 * shoulder_px))
    return int(cx * w), int(cy * h), radius


def pixelate_heads(frame_bgr: np.ndarray, people: Sequence[dict[str, Any]],
                   w: int, h: int) -> np.ndarray:
    """Return a copy of the frame with every head mosaiced out."""
    out = frame_bgr.copy()
    fh, fw = out.shape[:2]
    for person in people:
        cx, cy, r = head_circle(person, w, h)
        # head_circle works in the event's coordinate space; rescale if the frame
        # we are pixelating has since been resized.
        if (fw, fh) != (w, h):
            cx = int(cx * fw / max(1, w))
            cy = int(cy * fh / max(1, h))
            r = int(r * fw / max(1, w))
        x0, y0 = max(0, cx - r), max(0, cy - r)
        x1, y1 = min(fw, cx + r), min(fh, cy + r)
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        roi = out[y0:y1, x0:x1]
        small = cv2.resize(roi, (MOSAIC, MOSAIC), interpolation=cv2.INTER_LINEAR)
        out[y0:y1, x0:x1] = cv2.resize(
            small, (x1 - x0, y1 - y0), interpolation=cv2.INTER_NEAREST
        )
    return out


def encode_snapshot(frame_bgr: np.ndarray, people: Sequence[dict[str, Any]],
                    w: int, h: int) -> tuple[str, int, int]:
    """Pixelate, shrink and JPEG-encode a frame. Returns (base64, width, height)."""
    safe = pixelate_heads(frame_bgr, people, w, h)
    fh, fw = safe.shape[:2]
    if fw > SNAPSHOT_WIDTH:
        scale = SNAPSHOT_WIDTH / float(fw)
        safe = cv2.resize(safe, (SNAPSHOT_WIDTH, max(1, int(round(fh * scale)))),
                          interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", safe, [int(cv2.IMWRITE_JPEG_QUALITY), SNAPSHOT_QUALITY])
    if not ok:
        raise RuntimeError("jpeg encode failed")
    out_h, out_w = safe.shape[:2]
    return base64.b64encode(buf.tobytes()).decode("ascii"), out_w, out_h
