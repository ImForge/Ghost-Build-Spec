"""The evidence ring buffer: bounded, in RAM, and packable into a sealed ZIP."""
from __future__ import annotations

import io
import json
import zipfile

import numpy as np

from node.ringbuf import RingBuffer


def _frame(value=120):
    return np.full((480, 640, 3), value, dtype=np.uint8)


def test_buffer_is_bounded_by_seconds_times_fps():
    rb = RingBuffer("cam-01", seconds=4, fps=5)
    for i in range(100):
        rb.maybe_add(_frame(), ts=1000.0 + i * 0.2)
    assert len(rb) == 4 * 5


def test_frames_are_rate_limited_to_ring_fps():
    rb = RingBuffer("cam-01", seconds=30, fps=5)
    # 10 Hz input against a 5 Hz buffer: half the frames should be dropped.
    for i in range(20):
        rb.maybe_add(_frame(), ts=1000.0 + i * 0.1)
    assert len(rb) == 10, "a 10 Hz source into a 5 Hz buffer must halve exactly"


def test_oldest_frames_are_the_ones_dropped():
    rb = RingBuffer("cam-01", seconds=2, fps=5)
    for i in range(30):
        rb.maybe_add(_frame(i % 200), ts=1000.0 + i * 0.2)
    stamps = [ts for ts, _ in rb.snapshot()]
    assert stamps == sorted(stamps)
    assert stamps[0] > 1000.0


def test_zip_contains_meta_and_zero_padded_frames():
    rb = RingBuffer("cam-02", seconds=2, fps=5)
    for i in range(10):
        rb.maybe_add(_frame(), ts=2000.0 + i * 0.2)
    blob, meta = rb.to_zip("e_abc123")

    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = sorted(zf.namelist())
        assert "meta.json" in names
        frames = [n for n in names if n.startswith("frames/")]
        assert frames[0] == "frames/000001.jpg"
        assert len(frames) == meta["n_frames"]
        on_disk = json.loads(zf.read("meta.json"))

    assert on_disk["cam"] == "cam-02"
    assert on_disk["evidence_id"] == "e_abc123"
    assert on_disk["fps"] == 5
    assert on_disk["started_ts"] <= on_disk["ended_ts"]


def test_zip_of_an_empty_buffer_is_still_valid():
    rb = RingBuffer("cam-01", seconds=5, fps=5)
    blob, meta = rb.to_zip("e_empty")
    assert meta["n_frames"] == 0
    assert meta["started_ts"] is None
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        assert zf.namelist() == ["meta.json"]


def test_clear_empties_the_buffer():
    rb = RingBuffer("cam-01", seconds=5, fps=5)
    for i in range(10):
        rb.maybe_add(_frame(), ts=3000.0 + i * 0.3)
    assert len(rb) > 0
    rb.clear()
    assert len(rb) == 0


def test_stored_frames_are_decodable_jpeg():
    import cv2

    rb = RingBuffer("cam-01", seconds=2, fps=5)
    rb.maybe_add(_frame(77), ts=4000.0)
    _ts, jpg = rb.snapshot()[0]
    img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
    assert img is not None and img.shape[1] == 640
    # Evidence frames are deliberately NOT pixelated; a flat frame stays flat.
    assert abs(int(img.mean()) - 77) <= 2
