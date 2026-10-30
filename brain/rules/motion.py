"""Running and fighting — the two rules that are purely about how people move.

Both work in body-heights per second, so a threshold means the same thing for
someone next to the lens and someone at the far end of a corridor. Both also
require the behaviour to be *sustained*: one fast frame is tracker noise, a
second of it is a person.

Fighting is deliberately conservative. Flailing arms alone are a wave; two
people close together alone is a conversation. It takes both, held for a second,
and even then it is a prompt for a human to look rather than a conclusion.
"""
from __future__ import annotations

import logging
from itertools import combinations
from typing import TYPE_CHECKING, Any

from brain import geometry as geo
from brain.constants import KP_CONF_MIN, KP_LEFT_WRIST, KP_RIGHT_WRIST
from brain.rules import register
from brain.rules.base import Candidate, Rule

if TYPE_CHECKING:  # pragma: no cover
    from brain.config import ConfigStore
    from brain.state import CamState, TrackState

log = logging.getLogger("brain.rules.motion")

SPEED_PAIR_S = 0.5      # displacement is measured across pairs this far apart
FIGHT_IOU_MIN = 0.05


def _centroid_speeds(track: TrackState, since: float,
                     height: float) -> list[tuple[float, float]]:
    """[(t, bh/s)] measured over sample pairs at least SPEED_PAIR_S apart."""
    samples = [s for s in track.hist if s.t >= since - SPEED_PAIR_S]
    out: list[tuple[float, float]] = []
    anchor = 0
    for i in range(1, len(samples)):
        while samples[i].t - samples[anchor].t > SPEED_PAIR_S and anchor < i - 1:
            anchor += 1
        dt = samples[i].t - samples[anchor].t
        if dt < SPEED_PAIR_S * 0.8:
            continue
        speed = geo.speed_bh_s(samples[anchor].centre, samples[i].centre, dt, height)
        out.append((samples[i].t, speed))
    return [(t, v) for t, v in out if t >= since]


class RunningRule(Rule):
    type = "running"

    def evaluate(self, cam_state: CamState, rule_cfg: dict[str, Any], now: float,
                 config: ConfigStore) -> list[Candidate]:
        threshold = float(self.param(rule_cfg, "speed_bh_s", 1.5))
        sustain_s = float(self.param(rule_cfg, "sustain_s", 1.0))

        out: list[Candidate] = []
        for track in list(cam_state.tracks.values()):
            height = track.height()
            if height <= 1e-6 or track.age_s < sustain_s:
                continue
            speeds = _centroid_speeds(track, now - sustain_s, height)
            if len(speeds) < 2:
                continue
            mean_speed = sum(v for _t, v in speeds) / len(speeds)
            if mean_speed < threshold:
                continue
            out.append(Candidate(
                type=self.type,
                severity=str(rule_cfg.get("severity", "low")),
                cam=cam_state.cam,
                track_ids=[track.id],
                message=(f"Running on {config.cam_name(cam_state.cam)}: "
                         f"{mean_speed:.1f} body-heights/s for {sustain_s:g}s"),
                dedupe_key=f"running:{cam_state.cam}:{track.id}",
                features={"speed_bh_s": round(mean_speed, 2),
                          "sustain_s": sustain_s,
                          "samples": len(speeds)},
            ))
        return out


def _wrist_speed(track: TrackState, since: float, height: float) -> float:
    """Mean of each wrist's own displacement rate, in bh/s.

    Each wrist separately, then averaged — not the displacement of their
    midpoint, which would cancel out two hands moving in opposite directions.
    """
    samples = [s for s in track.hist if s.t >= since]
    if len(samples) < 2 or height <= 1e-6:
        return 0.0
    rates: list[float] = []
    for older, newer in zip(samples, samples[1:]):
        dt = newer.t - older.t
        if dt <= 0:
            continue
        for idx in (KP_LEFT_WRIST, KP_RIGHT_WRIST):
            if older.kp[idx][2] < KP_CONF_MIN or newer.kp[idx][2] < KP_CONF_MIN:
                continue
            rates.append(geo.speed_bh_s(
                (float(older.kp[idx][0]), float(older.kp[idx][1])),
                (float(newer.kp[idx][0]), float(newer.kp[idx][1])),
                dt, height,
            ))
    return sum(rates) / len(rates) if rates else 0.0


class FightRule(Rule):
    type = "fight"

    def evaluate(self, cam_state: CamState, rule_cfg: dict[str, Any], now: float,
                 config: ConfigStore) -> list[Candidate]:
        wrist_min = float(self.param(rule_cfg, "wrist_speed_bh_s", 2.0))
        sustain_s = float(self.param(rule_cfg, "sustain_s", 1.0))
        max_dist_bh = float(self.param(rule_cfg, "max_center_dist_bh", 0.6))

        tracks = [t for t in cam_state.tracks.values() if t.latest is not None]
        out: list[Candidate] = []
        live_pairs: set[tuple[int, int]] = set()

        for a, b in combinations(tracks, 2):
            key = (min(a.id, b.id), max(a.id, b.id))
            ha, hb = a.height(), b.height()
            if ha <= 1e-6 or hb <= 1e-6:
                continue
            sa, sb = a.latest, b.latest
            assert sa is not None and sb is not None  # filtered above

            mean_h = (ha + hb) / 2.0
            centre_dist = ((sa.centre[0] - sb.centre[0]) ** 2
                           + (sa.centre[1] - sb.centre[1]) ** 2) ** 0.5
            close = (centre_dist <= max_dist_bh * mean_h
                     or geo.bbox_iou(sa.bbox, sb.bbox) > FIGHT_IOU_MIN)
            if not close:
                cam_state.pair_since.pop(key, None)
                continue

            live_pairs.add(key)
            since = cam_state.pair_since.setdefault(key, now)
            if now - since < sustain_s:
                continue

            wa = _wrist_speed(a, now - sustain_s, ha)
            wb = _wrist_speed(b, now - sustain_s, hb)
            if wa < wrist_min or wb < wrist_min:
                continue

            out.append(Candidate(
                type=self.type,
                severity=str(rule_cfg.get("severity", "critical")),
                cam=cam_state.cam,
                track_ids=[key[0], key[1]],
                message=(f"Possible fight on {config.cam_name(cam_state.cam)}: "
                         f"two people together for {now - since:.1f}s with arms "
                         f"moving at {min(wa, wb):.1f} body-heights/s"),
                dedupe_key=f"fight:{cam_state.cam}:{key[0]}:{key[1]}",
                features={"wrist_a_bh_s": round(wa, 2), "wrist_b_bh_s": round(wb, 2),
                          "centre_dist_bh": round(centre_dist / mean_h, 2),
                          "together_s": round(now - since, 2)},
            ))

        for key in [k for k in cam_state.pair_since if k not in live_pairs]:
            cam_state.pair_since.pop(key, None)
        return out


register(RunningRule())
register(FightRule())
