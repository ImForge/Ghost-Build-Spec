"""Pure geometry: feet, zones, line crossings, floor mapping, crowding.

Nothing here touches shared state, which is why it is the one module with a
dedicated test file. Two conventions hold throughout:

* camera coordinates are normalized 0-1 with a top-left origin;
* floor coordinates are pixels on the 1600x1000 floor plan.

Every speed threshold in the rule engine is expressed in body-heights per second,
which is what `track_height` exists to supply: it makes a threshold mean the same
thing for someone close to the lens and someone across the quad.
"""
from __future__ import annotations

import logging
import math
from statistics import median
from typing import Any, Iterable, Sequence

import numpy as np
from shapely.geometry import Point, Polygon

from brain.constants import FLOOR_H, FLOOR_W, KP_CONF_MIN

log = logging.getLogger("brain.geometry")

XY = tuple[float, float]
BBox = tuple[float, float, float, float]

_polygon_cache: dict[str, tuple[tuple[XY, ...], Polygon]] = {}


# --- points -------------------------------------------------------------
def foot_point(bbox: Sequence[float]) -> XY:
    """Bottom-centre of the bounding box: where the person meets the floor."""
    x, y, w, h = (float(v) for v in bbox[:4])
    return x + w / 2.0, y + h


def centroid(bbox: Sequence[float]) -> XY:
    x, y, w, h = (float(v) for v in bbox[:4])
    return x + w / 2.0, y + h / 2.0


def kp_mean(kp: Any, indices: Iterable[int],
            conf_min: float = KP_CONF_MIN) -> XY | None:
    """Mean of the named keypoints, or None if none of them are trustworthy."""
    pts = []
    for i in indices:
        try:
            x, y, c = (float(v) for v in kp[i][:3])
        except (IndexError, TypeError, ValueError):
            continue
        if c >= conf_min:
            pts.append((x, y))
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


# --- polygons -----------------------------------------------------------
def get_polygon(zone_id: str, points: Sequence[Sequence[float]]) -> Polygon:
    """Cached shapely polygon. Rebuilt when the zone's points actually change."""
    key = tuple((float(p[0]), float(p[1])) for p in points)
    cached = _polygon_cache.get(zone_id)
    if cached is not None and cached[0] == key:
        return cached[1]
    poly = Polygon(key)
    if not poly.is_valid:
        # A self-intersecting polygon drawn in the editor would otherwise make
        # every containment test throw; buffer(0) repairs the common cases.
        poly = poly.buffer(0)
    _polygon_cache[zone_id] = (key, poly)
    return poly


def clear_polygon_cache() -> None:
    _polygon_cache.clear()


def point_in_polygon(point: XY, zone_id: str,
                     points: Sequence[Sequence[float]]) -> bool:
    if len(points) < 3:
        return False
    poly = get_polygon(zone_id, points)
    if poly.is_empty:
        return False
    p = Point(float(point[0]), float(point[1]))
    # covers() rather than contains() so someone standing exactly on a taped
    # boundary line counts as inside rather than flickering.
    return bool(poly.covers(p))


def polygon_centroid(zone_id: str, points: Sequence[Sequence[float]]) -> XY:
    poly = get_polygon(zone_id, points)
    if poly.is_empty:
        return 0.5, 0.5
    c = poly.centroid
    return float(c.x), float(c.y)


# --- lines --------------------------------------------------------------
def side(a: Sequence[float], b: Sequence[float], p: Sequence[float]) -> int:
    """Which side of line a->b the point p is on: -1, 0 or +1."""
    cross = ((float(b[0]) - float(a[0])) * (float(p[1]) - float(a[1]))
             - (float(b[1]) - float(a[1])) * (float(p[0]) - float(a[0])))
    if cross > 0:
        return 1
    if cross < 0:
        return -1
    return 0


def _orientation(p: Sequence[float], q: Sequence[float], r: Sequence[float]) -> int:
    return side(p, q, r)


def _on_segment(p: Sequence[float], q: Sequence[float], r: Sequence[float]) -> bool:
    """True when q lies on the closed segment p-r (used for collinear cases)."""
    return (min(p[0], r[0]) <= q[0] <= max(p[0], r[0])
            and min(p[1], r[1]) <= q[1] <= max(p[1], r[1]))


def segments_intersect(p1: Sequence[float], p2: Sequence[float],
                       q1: Sequence[float], q2: Sequence[float]) -> bool:
    """Standard orientation test, collinear overlaps included."""
    o1 = _orientation(p1, p2, q1)
    o2 = _orientation(p1, p2, q2)
    o3 = _orientation(q1, q2, p1)
    o4 = _orientation(q1, q2, p2)
    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and _on_segment(p1, q1, p2):
        return True
    if o2 == 0 and _on_segment(p1, q2, p2):
        return True
    if o3 == 0 and _on_segment(q1, p1, q2):
        return True
    if o4 == 0 and _on_segment(q1, p2, q2):
        return True
    return False


def crossed_line(prev_foot: XY, cur_foot: XY, a: Sequence[float], b: Sequence[float],
                 direction: str = "any") -> bool:
    """Did the walker cross this line between two samples, in the right direction?

    Both tests are needed: the side sign flips anywhere along the infinite line,
    so without the segment test someone walking past the end of a doorway would
    register as walking through it.
    """
    prev_side = side(a, b, prev_foot)
    cur_side = side(a, b, cur_foot)
    if prev_side == cur_side:
        return False
    if not segments_intersect(prev_foot, cur_foot, a, b):
        return False
    if direction == "pos":
        return prev_side <= 0 and cur_side > 0
    if direction == "neg":
        return prev_side >= 0 and cur_side < 0
    return True


# --- floor mapping ------------------------------------------------------
def to_floor(point: Sequence[float], homography: Sequence[Sequence[float]] | None,
             default_rect: Sequence[float] | None) -> XY:
    """Camera-normalized point to floor-plan pixels.

    With a homography, this is a real perspective mapping. Without one, the
    camera's view is stretched linearly into its `default_rect` — wrong in
    detail but good enough to see who is where, and it keeps every cam visible
    on the twin before anyone has calibrated anything.
    """
    x, y = float(point[0]), float(point[1])
    if homography is not None:
        h = np.asarray(homography, dtype=float).reshape(3, 3)
        vec = h @ np.array([x, y, 1.0])
        w = float(vec[2])
        if abs(w) < 1e-9:
            log.debug("degenerate homography result, falling back to default_rect")
        else:
            return float(vec[0] / w), float(vec[1] / w)
    rx, ry, rw, rh = (
        (float(v) for v in default_rect[:4]) if default_rect
        else (0.0, 0.0, float(FLOOR_W), float(FLOOR_H))
    )
    return rx + x * rw, ry + y * rh


def homography_from_points(cam_pts: Sequence[Sequence[float]],
                           floor_pts: Sequence[Sequence[float]]) -> list[list[float]]:
    """Four matched point pairs to a 3x3 matrix, via OpenCV."""
    import cv2

    if len(cam_pts) != 4 or len(floor_pts) != 4:
        raise ValueError("calibration needs exactly 4 points on each side")
    src = np.asarray(cam_pts, dtype=np.float32).reshape(4, 2)
    dst = np.asarray(floor_pts, dtype=np.float32).reshape(4, 2)
    matrix = cv2.getPerspectiveTransform(src, dst)
    if not np.all(np.isfinite(matrix)):
        raise ValueError("those four points are degenerate (collinear or repeated)")
    return [[float(v) for v in row] for row in matrix]


# --- crowding -----------------------------------------------------------
def nn_mean_dist_m(points_floor: Sequence[XY], px_per_m: float) -> float | None:
    """Mean nearest-neighbour distance in metres. None with fewer than 2 points."""
    n = len(points_floor)
    if n < 2 or px_per_m <= 0:
        return None
    total = 0.0
    for i, p in enumerate(points_floor):
        best = math.inf
        for j, q in enumerate(points_floor):
            if i == j:
                continue
            best = min(best, math.hypot(p[0] - q[0], p[1] - q[1]))
        total += best
    return (total / n) / px_per_m


def bbox_iou(a: Sequence[float], b: Sequence[float]) -> float:
    """IoU of two top-left [x, y, w, h] boxes."""
    ax, ay, aw, ah = (float(v) for v in a[:4])
    bx, by, bw, bh = (float(v) for v in b[:4])
    x0, y0 = max(ax, bx), max(ay, by)
    x1, y1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


# --- body height --------------------------------------------------------
def track_height(heights: Sequence[float], minimum: int = 3) -> float:
    """Median bbox height over a track's history — the bh/s unit's denominator.

    The median rather than the latest value because a single frame where the
    detector clipped someone's legs would otherwise double every speed on that
    track for as long as it lasted.
    """
    usable = [float(h) for h in heights if h and float(h) > 1e-6]
    if len(usable) < minimum:
        return float(usable[-1]) if usable else 0.0
    return float(median(usable))


def speed_bh_s(p0: XY, p1: XY, dt: float, height: float) -> float:
    """Displacement between two points as body-heights per second."""
    if dt <= 0 or height <= 1e-6:
        return 0.0
    return math.hypot(p1[0] - p0[0], p1[1] - p0[1]) / (height * dt)


def torso_angle_deg(shoulder: XY, hip: XY) -> float:
    """0 degrees is upright, 90 is flat on the floor."""
    dx = abs(shoulder[0] - hip[0])
    dy = abs(shoulder[1] - hip[1])
    if dx == 0.0 and dy == 0.0:
        return 0.0
    return math.degrees(math.atan2(dx, dy))
