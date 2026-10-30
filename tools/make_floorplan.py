"""Generate the top-down floor plan the digital twin is drawn on.

The plan is a plain 1600x1000 PNG at 40 px per metre. Camera `default_rect`
values in config/cams.yaml are coordinates on this image, so if you change the
room rectangles here, change them there too.

    python tools/make_floorplan.py
    python tools/make_floorplan.py --out somewhere/else.png
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger("tools.make_floorplan")

W, H = 1600, 1000
PX_PER_M = 40

BG = (232, 234, 237)
ROOM_FILL = (248, 249, 250)
OUTLINE = (52, 58, 64)
GRID = (218, 221, 225)
LABEL = (73, 80, 87)

# name, x, y, w, h — these mirror config/cams.yaml default_rect values.
ROOMS: list[tuple[str, int, int, int, int]] = [
    ("Corridor B", 100, 100, 600, 400),
    ("Main Gate", 900, 100, 600, 400),
    ("Library Stairs", 100, 550, 600, 350),
    ("Quad", 900, 550, 600, 350),
]


def _font(size: int) -> ImageFont.ImageFont:
    """A readable font wherever this runs; PIL's default if none are installed."""
    for name in (
        "DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _text_size(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return right - left, bottom - top


def draw_floorplan() -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # 1 m grid, so distances on the twin are readable by eye.
    for x in range(0, W + 1, PX_PER_M):
        d.line([(x, 0), (x, H)], fill=GRID, width=1)
    for y in range(0, H + 1, PX_PER_M):
        d.line([(0, y), (W, y)], fill=GRID, width=1)

    title_font, label_font, small_font = _font(34), _font(26), _font(20)

    for name, x, y, w, h in ROOMS:
        d.rectangle([x, y, x + w, y + h], fill=ROOM_FILL, outline=OUTLINE, width=4)
        tw, th = _text_size(d, name, label_font)
        d.text((x + (w - tw) / 2, y + 16), name, fill=LABEL, font=label_font)
        dims = f"{w / PX_PER_M:.0f} x {h / PX_PER_M:.0f} m"
        dw, _ = _text_size(d, dims, small_font)
        d.text((x + (w - dw) / 2, y + h - 34), dims, fill=LABEL, font=small_font)

    d.rectangle([2, 2, W - 3, H - 3], outline=OUTLINE, width=5)

    # North arrow, top-right.
    nx, ny = W - 90, 60
    d.polygon([(nx, ny - 38), (nx - 16, ny + 14), (nx, ny + 2), (nx + 16, ny + 14)],
              fill=OUTLINE)
    d.text((nx - 11, ny + 20), "N", fill=OUTLINE, font=label_font)

    # Scale bar: 10 m = 400 px, alternating blocks every 2 m.
    bx, by, bar = 100, H - 60, 10 * PX_PER_M
    for i in range(5):
        seg = bar // 5
        d.rectangle([bx + i * seg, by, bx + (i + 1) * seg, by + 16],
                    fill=OUTLINE if i % 2 == 0 else ROOM_FILL, outline=OUTLINE, width=2)
    d.text((bx, by - 30), "0", fill=LABEL, font=small_font)
    d.text((bx + bar - 24, by - 30), "10 m", fill=LABEL, font=small_font)
    d.text((bx + bar + 24, by - 4), f"{PX_PER_M} px / m", fill=LABEL, font=small_font)

    d.text((100, 40), "GHOST — campus floor plan", fill=OUTLINE, font=title_font)
    return img


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, stream=sys.stdout,
        format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s", datefmt="%H:%M:%S",
    )
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description="Render the twin's floor plan PNG.")
    ap.add_argument("--out", type=Path, default=root / "dashboard" / "public" / "floorplan.png")
    args = ap.parse_args(argv)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    img = draw_floorplan()
    img.save(args.out, "PNG", optimize=True)
    log.info("wrote %s (%dx%d, %d px/m)", args.out, W, H, PX_PER_M)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
