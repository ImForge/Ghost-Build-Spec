"""Render a short test video so capture, the motion gate and the publish path can
be exercised on a machine with no webcam.

A moving white rectangle on grey. The pose model will find no people in it, which
is the point: it proves the pipeline runs end to end with zero detections, and it
gives the motion gate something to switch on and off (the rectangle stops moving
for a second in the middle).

    python tools/make_test_video.py
    python tools/make_test_video.py --out /tmp/clip.mp4 --seconds 10
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("tools.make_test_video")


def render(out: Path, seconds: float, fps: float, w: int, h: int) -> int:
    """A big fast-bouncing block, frozen in the middle third.

    It has to be large and quick: the motion gate scores the mean absolute
    difference over the whole frame, so a small slow object scores well under the
    4.0 default and the idle path would be the only one ever tested.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    if not writer.isOpened():
        raise RuntimeError(f"cannot open a writer for {out}")

    total = int(seconds * fps)
    still_from, still_to = int(total * 0.4), int(total * 0.6)
    box = 220
    span = w - box - 40
    period = 18.0  # frames per sweep -> ~40 px/frame, comfortably above the gate

    for i in range(total):
        frame = np.full((h, w, 3), 90, dtype=np.uint8)
        # Freeze in the middle third so the gate has to drop to idle_fps.
        j = still_from if still_from <= i < still_to else i
        phase = (j % period) / period
        tri = 2 * phase if phase < 0.5 else 2 * (1 - phase)  # 0 -> 1 -> 0
        x = int(20 + tri * span)
        y = int(h / 2 - box / 2 + 60 * np.sin(j * 0.4))
        cv2.rectangle(frame, (x, y), (x + box, y + box), (235, 235, 235), -1)
        cv2.putText(frame, f"{i:04d}", (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                    (200, 200, 200), 2, cv2.LINE_AA)
        writer.write(frame)
    writer.release()
    return total


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, stream=sys.stdout,
        format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s", datefmt="%H:%M:%S",
    )
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description="Render a synthetic test video.")
    ap.add_argument("--out", type=Path, default=root / "brain" / "data" / "test_clip.mp4")
    ap.add_argument("--seconds", type=float, default=8.0)
    ap.add_argument("--fps", type=float, default=15.0)
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    args = ap.parse_args(argv)

    n = render(args.out, args.seconds, args.fps, args.width, args.height)
    log.info("wrote %s — %d frames at %g fps", args.out, n, args.fps)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
