"""Head pixelation. A snapshot that leaks a face breaks the whole premise."""
from __future__ import annotations

import base64

import cv2
import numpy as np
import pytest

from node.privacy import MOSAIC, encode_snapshot, head_circle, pixelate_heads

W, H = 640, 480


def _person(nose=(0.5, 0.15, 0.9), shoulders=((0.42, 0.25, 0.9), (0.58, 0.25, 0.9)),
            face=None, bbox=(0.40, 0.10, 0.20, 0.60)):
    kp = [[0.0, 0.0, 0.0] for _ in range(17)]
    kp[0] = list(nose)
    for idx, value in (face or {}).items():
        kp[idx] = list(value)
    if shoulders:
        kp[5], kp[6] = list(shoulders[0]), list(shoulders[1])
    return {"bbox": list(bbox), "kp": kp}


def _noise(seed=0):
    return np.random.default_rng(seed).integers(0, 255, (H, W, 3), dtype=np.uint8)


def test_nose_is_preferred_when_confident():
    cx, cy, _r = head_circle(_person(nose=(0.5, 0.15, 0.9)), W, H)
    assert (cx, cy) == (int(0.5 * W), int(0.15 * H))


def test_falls_back_to_eyes_and_ears_when_the_nose_is_missing():
    person = _person(nose=(0.0, 0.0, 0.0), face={1: (0.40, 0.20, 0.8), 2: (0.60, 0.20, 0.8)})
    cx, cy, _r = head_circle(person, W, H)
    assert cx == int(0.5 * W)
    assert cy == int(0.20 * H)


def test_falls_back_to_the_top_of_the_bbox_when_no_face_keypoint_survives():
    """A person facing away still gets covered — this is the case that matters."""
    person = _person(nose=(0.0, 0.0, 0.0), shoulders=None, bbox=(0.4, 0.1, 0.2, 0.6))
    cx, cy, r = head_circle(person, W, H)
    assert cx == int(0.5 * W)
    assert cy == int((0.1 + 0.6 * 0.08) * H)
    assert r > 0


def test_radius_follows_shoulder_width():
    narrow = head_circle(_person(shoulders=((0.48, 0.25, 0.9), (0.52, 0.25, 0.9))), W, H)[2]
    wide = head_circle(_person(shoulders=((0.30, 0.25, 0.9), (0.70, 0.25, 0.9))), W, H)[2]
    assert wide > narrow


def test_radius_never_drops_below_the_floor():
    """A distant person must not get a 2-pixel mosaic that reveals everything."""
    tiny = _person(shoulders=((0.499, 0.25, 0.9), (0.501, 0.25, 0.9)), bbox=(0.49, 0.2, 0.02, 0.06))
    assert head_circle(tiny, W, H)[2] >= 20


def test_pixelation_destroys_detail_in_the_head_region():
    frame = _noise()
    out = pixelate_heads(frame, [_person()], W, H)
    cx, cy, r = head_circle(_person(), W, H)
    x0, y0 = max(0, cx - r), max(0, cy - r)
    x1, y1 = min(W, cx + r), min(H, cy + r)

    before = frame[y0:y1, x0:x1].reshape(-1, 3)
    after = out[y0:y1, x0:x1].reshape(-1, 3)
    assert len(np.unique(before, axis=0)) > 1000
    # An 8x8 mosaic can hold at most 64 distinct colours.
    assert len(np.unique(after, axis=0)) <= MOSAIC * MOSAIC


def test_pixelation_leaves_the_rest_of_the_frame_byte_identical():
    frame = _noise(1)
    out = pixelate_heads(frame, [_person()], W, H)
    cx, cy, r = head_circle(_person(), W, H)
    y0, y1 = max(0, cy - r), min(H, cy + r)
    assert np.array_equal(np.delete(frame, np.s_[y0:y1], axis=0),
                          np.delete(out, np.s_[y0:y1], axis=0))


def test_input_frame_is_not_mutated():
    frame = _noise(2)
    original = frame.copy()
    pixelate_heads(frame, [_person()], W, H)
    assert np.array_equal(frame, original)


def test_heads_outside_the_frame_are_clamped_not_crashed():
    for bbox in ((0.0, -0.4, 0.2, 0.6), (0.95, 0.9, 0.2, 0.3), (-0.1, 0.0, 0.2, 0.3)):
        person = _person(nose=(bbox[0], bbox[1], 0.9), bbox=bbox)
        assert pixelate_heads(_noise(3), [person], W, H).shape == (H, W, 3)


def test_every_person_gets_pixelated():
    frame = _noise(4)
    people = [_person(nose=(x, 0.2, 0.9), bbox=(x - 0.05, 0.15, 0.1, 0.5))
              for x in (0.2, 0.5, 0.8)]
    out = pixelate_heads(frame, people, W, H)
    for person in people:
        cx, cy, r = head_circle(person, W, H)
        x0, y0 = max(0, cx - r), max(0, cy - r)
        x1, y1 = min(W, cx + r), min(H, cy + r)
        patch = out[y0:y1, x0:x1].reshape(-1, 3)
        assert len(np.unique(patch, axis=0)) <= MOSAIC * MOSAIC


def test_snapshot_is_capped_at_320_px_and_decodes():
    b64, w, h = encode_snapshot(_noise(5), [_person()], W, H)
    assert w <= 320
    jpg = base64.b64decode(b64)
    img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
    assert (img.shape[1], img.shape[0]) == (w, h)


def test_snapshot_of_an_empty_scene_still_works():
    b64, w, _h = encode_snapshot(_noise(6), [], W, H)
    assert w == 320 and base64.b64decode(b64)


def test_pixelation_rescales_when_the_frame_was_resized_after_the_event():
    """The event is in the capture's coordinates; the frame may be smaller by now."""
    small = np.random.default_rng(7).integers(0, 255, (240, 320, 3), dtype=np.uint8)
    out = pixelate_heads(small, [_person()], W, H)
    cx, cy, r = head_circle(_person(), W, H)
    sx, sy, sr = int(cx * 320 / W), int(cy * 240 / H), int(r * 320 / W)
    patch = out[max(0, sy - sr):sy + sr, max(0, sx - sr):sx + sr].reshape(-1, 3)
    assert len(np.unique(patch, axis=0)) <= MOSAIC * MOSAIC
