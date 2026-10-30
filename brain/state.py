"""Live state: what each camera can see right now.

Everything here is guarded by one RLock held by `World`. Critical sections are
deliberately tiny — the MQTT thread, the 10 Hz engine, the anomaly worker,
housekeeping and every HTTP handler all contend for it.

Two things are worth knowing:

* `t` on a Sample is the brain's receive time, never the node's `ts`. Phones on
  a hotspot drift by seconds (docs/DECISIONS.md D2).
* zone membership and line sides are computed once per event here, so a rule
  evaluating at 10 Hz never re-does point-in-polygon work.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import numpy as np

from brain import geometry as geo
from brain.constants import (
    CAM_ACTIVE_S, COUNT_HIST_S, CROSSING_HIST_S, HIST_MAXLEN, KEYPOINTS,
    KP_LEFT_HIP, KP_LEFT_SHOULDER, KP_RIGHT_HIP, KP_RIGHT_SHOULDER,
    TRACK_TTL_FALL_S, TRACK_TTL_S,
)

log = logging.getLogger("brain.state")

ZONE_EXIT_GRACE_S = 2.0  # how long outside a zone before a loiter timer resets


@dataclass
class Sample:
    """One observation of one person, in camera-normalized coordinates."""

    t: float
    bbox: tuple[float, float, float, float]
    kp: np.ndarray  # (17, 3)

    @property
    def foot(self) -> geo.XY:
        return geo.foot_point(self.bbox)

    @property
    def centre(self) -> geo.XY:
        return geo.centroid(self.bbox)

    @property
    def height(self) -> float:
        return float(self.bbox[3])

    @property
    def aspect(self) -> float:
        h = float(self.bbox[3])
        return float(self.bbox[2]) / h if h > 1e-6 else 0.0

    def hip(self) -> geo.XY:
        """Hip midpoint, falling back to the bbox centre when hips are hidden."""
        pt = geo.kp_mean(self.kp, (KP_LEFT_HIP, KP_RIGHT_HIP))
        return pt if pt is not None else self.centre

    def shoulder(self) -> geo.XY:
        pt = geo.kp_mean(self.kp, (KP_LEFT_SHOULDER, KP_RIGHT_SHOULDER))
        return pt if pt is not None else self.centre


@dataclass
class TrackState:
    """One tracked person on one camera, with the history rules need."""

    cam: str
    id: int
    hist: deque[Sample] = field(default_factory=lambda: deque(maxlen=HIST_MAXLEN))
    first_seen: float = 0.0
    last_seen: float = 0.0

    in_zones: set[str] = field(default_factory=set)
    zone_enter: dict[str, float] = field(default_factory=dict)
    zone_left: dict[str, float] = field(default_factory=dict)
    last_side: dict[str, int] = field(default_factory=dict)

    # Rule scratch space. `drop_at` also extends the track's time-to-live so a
    # person who fell and is no longer being detected is not forgotten mid-alert.
    drop_at: float | None = None

    @property
    def latest(self) -> Sample | None:
        return self.hist[-1] if self.hist else None

    @property
    def age_s(self) -> float:
        return self.last_seen - self.first_seen

    def height(self) -> float:
        return geo.track_height([s.height for s in self.hist])

    def samples_since(self, t: float) -> list[Sample]:
        return [s for s in self.hist if s.t >= t]

    def displacement_since(self, t: float) -> float:
        """Largest distance between any two centroids after `t` (stillness test)."""
        pts = [s.centre for s in self.samples_since(t)]
        if len(pts) < 2:
            return 0.0
        return max(
            ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
            for i, a in enumerate(pts) for b in pts[i + 1:]
        )


@dataclass
class CamState:
    """Everything known about one camera."""

    cam: str
    tracks: dict[int, TrackState] = field(default_factory=dict)
    last_event: float = 0.0
    seq: int = 0
    w: int = 640
    h: int = 480

    count_hist: dict[str, deque[tuple[float, int]]] = field(default_factory=dict)
    # (t, track_id, direction) where direction is +1 for a crossing whose side
    # sign went negative -> positive and -1 for the reverse. §7.2 describes a
    # 2-tuple; the sign is carried so the tripwire rule can filter by direction
    # from this authoritative, deduplicated record (docs/DECISIONS.md D8).
    crossings: dict[str, deque[tuple[float, int, int]]] = field(default_factory=dict)
    pair_since: dict[tuple[int, int], float] = field(default_factory=dict)
    # Rule bookkeeping for discrete events: key -> the timestamp of the last
    # occurrence already reported. Crossings, tailgate bursts and zone entries
    # are events, not conditions, so each must raise exactly one candidate
    # however many engine ticks it stays visible for (docs/DECISIONS.md D9).
    rule_marks: dict[str, float] = field(default_factory=dict)

    def already_reported(self, key: str, occurred_at: float) -> bool:
        """True if this occurrence has been reported; otherwise mark and return False."""
        if self.rule_marks.get(key, 0.0) >= occurred_at:
            return True
        self.rule_marks[key] = occurred_at
        return False

    health: dict[str, Any] = field(default_factory=dict)
    snapshot_jpg: bytes | None = None
    learning: bool = False
    anomaly_flags: deque[bool] = field(default_factory=lambda: deque(maxlen=5))
    anomaly_explain: list[dict[str, Any]] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return (time.time() - self.last_event) <= CAM_ACTIVE_S

    @property
    def online(self) -> bool:
        return bool(self.health.get("online", False))

    def count(self) -> int:
        return len(self.tracks)

    def record_count(self, zone_id: str, now: float, count: int) -> None:
        hist = self.count_hist.setdefault(zone_id, deque())
        hist.append((now, count))
        cutoff = now - COUNT_HIST_S
        while hist and hist[0][0] < cutoff:
            hist.popleft()

    def count_at(self, zone_id: str, when: float) -> int | None:
        """Earliest recorded count at or after `when`, for rate-of-change."""
        hist = self.count_hist.get(zone_id)
        if not hist:
            return None
        for t, c in hist:
            if t >= when:
                return c
        return None

    def record_crossing(self, line_id: str, now: float, track_id: int,
                        direction: int = 0) -> None:
        hist = self.crossings.setdefault(line_id, deque())
        hist.append((now, track_id, direction))
        cutoff = now - CROSSING_HIST_S
        while hist and hist[0][0] < cutoff:
            hist.popleft()

    def recent_crossings(self, line_id: str, window_s: float,
                         now: float | None = None) -> list[tuple[float, int, int]]:
        now = time.time() if now is None else now
        return [c for c in self.crossings.get(line_id, ()) if c[0] >= now - window_s]

    def prune(self, now: float) -> list[int]:
        """Drop tracks nobody has seen lately. Returns the ids removed."""
        gone = [
            tid for tid, tr in self.tracks.items()
            if now - tr.last_seen > (TRACK_TTL_FALL_S if tr.drop_at else TRACK_TTL_S)
        ]
        for tid in gone:
            self.tracks.pop(tid, None)
        if gone:
            stale = {k for k in self.pair_since if k[0] in gone or k[1] in gone}
            for key in stale:
                self.pair_since.pop(key, None)
        return gone


class World:
    """All camera state behind one lock."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.cams: dict[str, CamState] = {}
        self.started = time.time()

    def cam(self, cam_id: str) -> CamState:
        with self.lock:
            state = self.cams.get(cam_id)
            if state is None:
                state = CamState(cam=cam_id)
                self.cams[cam_id] = state
                log.info("first sighting of %s", cam_id)
            return state

    def active_cams(self) -> list[str]:
        with self.lock:
            return [c for c, s in self.cams.items() if s.active]

    def ingest(self, event: dict[str, Any], zones: Sequence[dict[str, Any]],
               lines: Sequence[dict[str, Any]], now: float | None = None) -> CamState:
        """Fold one skeleton event into state and refresh derived geometry."""
        cam_id = str(event.get("cam", ""))
        now = time.time() if now is None else now
        state = self.cam(cam_id)

        with self.lock:
            state.last_event = now
            state.seq = int(event.get("seq", state.seq))
            state.w = int(event.get("w", state.w) or state.w)
            state.h = int(event.get("h", state.h) or state.h)

            seen: set[int] = set()
            for person in event.get("people") or []:
                try:
                    tid = int(person["id"])
                    bbox = tuple(float(v) for v in person["bbox"][:4])
                except (KeyError, TypeError, ValueError, IndexError):
                    log.debug("[%s] skipping malformed person record", cam_id)
                    continue
                seen.add(tid)

                track = state.tracks.get(tid)
                if track is None:
                    track = TrackState(cam=cam_id, id=tid, first_seen=now)
                    state.tracks[tid] = track
                track.last_seen = now
                track.hist.append(Sample(t=now, bbox=bbox, kp=_kp_array(person.get("kp"))))
                _update_geometry(state, track, zones, lines, now)

            state.prune(now)
            return state

    def set_health(self, cam_id: str, health: dict[str, Any]) -> None:
        state = self.cam(cam_id)
        with self.lock:
            state.health = dict(health)

    def snapshot_summary(self) -> list[dict[str, Any]]:
        with self.lock:
            return [
                {
                    "cam": s.cam, "online": s.online, "active": s.active,
                    "count": s.count(), "last_event": s.last_event,
                    "learning": s.learning, "health": dict(s.health),
                }
                for s in self.cams.values()
            ]


def _kp_array(kp: Any) -> np.ndarray:
    """Always a (17, 3) float array, whatever the node sent."""
    arr = np.zeros((KEYPOINTS, 3), dtype=float)
    if not kp:
        return arr
    for i, point in enumerate(kp[:KEYPOINTS]):
        try:
            arr[i] = [float(point[0]), float(point[1]), float(point[2])]
        except (IndexError, TypeError, ValueError):
            continue
    return arr


def _update_geometry(state: CamState, track: TrackState,
                     zones: Iterable[dict[str, Any]], lines: Iterable[dict[str, Any]],
                     now: float) -> None:
    """Refresh zone membership and line sides for one track."""
    sample = track.latest
    if sample is None:
        return
    foot = sample.foot

    for zone in zones:
        if zone.get("cam") != state.cam:
            continue
        zid = str(zone["id"])
        inside = geo.point_in_polygon(foot, zid, zone.get("polygon") or [])
        was_inside = zid in track.in_zones
        if inside:
            track.in_zones.add(zid)
            track.zone_left.pop(zid, None)
            if not was_inside:
                track.zone_enter.setdefault(zid, now)
        else:
            track.in_zones.discard(zid)
            if was_inside:
                track.zone_left[zid] = now
            # A brief detection gap should not reset a loiter timer, so the
            # entry time only clears after a continuous spell outside.
            left_at = track.zone_left.get(zid)
            if left_at is not None and now - left_at > ZONE_EXIT_GRACE_S:
                track.zone_enter.pop(zid, None)

    prev = track.hist[-2] if len(track.hist) >= 2 else None
    for line in lines:
        if line.get("cam") != state.cam:
            continue
        lid = str(line["id"])
        a, b = line.get("a") or [0, 0], line.get("b") or [0, 0]
        current = geo.side(a, b, foot)
        if prev is not None:
            went = geo.crossing_direction(prev.foot, foot, a, b)
            if went:
                state.record_crossing(lid, now, track.id, went)
        track.last_side[lid] = current
