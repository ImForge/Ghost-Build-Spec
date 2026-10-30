"""Fall detection.

The hard part is not spotting someone on the floor — it is not shouting every
time somebody sits down. Four conditions have to hold together:

1. the hips dropped fast (drop_rate, in body-heights per second);
2. the torso is now closer to horizontal than vertical (torso_angle);
3. the bounding box is wider than it is tall (aspect);
4. and they have not moved since (still).

Sitting fails 2 and 3. Lying down to stretch fails 1. Walking out of frame fails
4. On top of that, anything whose feet are on marked furniture is suppressed
outright, because a bench is where sitting legitimately looks like collapsing.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from brain import geometry as geo
from brain.rules import register
from brain.rules.base import Candidate, Rule

if TYPE_CHECKING:  # pragma: no cover
    from brain.config import ConfigStore
    from brain.state import CamState, TrackState

log = logging.getLogger("brain.rules.fall")

MIN_SAMPLES = 6
DROP_WINDOW_S = 2.5     # how far back to look for the drop
FALL_VALID_S = 4.0      # a drop older than this is no longer news
STILL_DELAY_S = 0.5     # ignore the moment of impact itself when judging stillness


class FallRule(Rule):
    type = "fall"

    def evaluate(self, cam_state: CamState, rule_cfg: dict[str, Any], now: float,
                 config: ConfigStore) -> list[Candidate]:
        drop_rate_min = float(self.param(rule_cfg, "drop_rate", 1.0))
        angle_min = float(self.param(rule_cfg, "torso_angle_deg", 60))
        aspect_min = float(self.param(rule_cfg, "aspect_min", 1.2))
        still_s_min = float(self.param(rule_cfg, "still_s", 1.5))
        still_max_disp = float(self.param(rule_cfg, "still_max_disp", 0.02))

        out: list[Candidate] = []
        for track in list(cam_state.tracks.values()):
            if len(track.hist) < MIN_SAMPLES:
                continue
            latest = track.latest
            if latest is None:
                continue

            height = track.height()
            drop_rate, drop_at = _worst_drop(track, now, height)
            if drop_at is not None and drop_rate >= drop_rate_min:
                track.drop_at = drop_at
            if track.drop_at is None or now - track.drop_at > FALL_VALID_S:
                continue

            torso = geo.torso_angle_deg(latest.shoulder(), latest.hip())
            aspect = latest.aspect
            still_span, displacement = _stillness(track, track.drop_at + STILL_DELAY_S)
            is_still = still_span >= still_s_min and displacement <= still_max_disp

            if not (torso >= angle_min and aspect >= aspect_min and is_still):
                continue
            if self.in_furniture(config, cam_state.cam, latest.foot):
                log.debug("[%s] suppressing fall for track %d: feet on furniture",
                          cam_state.cam, track.id)
                continue

            rate = max(drop_rate, 0.0)
            out.append(Candidate(
                type=self.type,
                severity=str(rule_cfg.get("severity", "critical")),
                cam=cam_state.cam,
                track_ids=[track.id],
                message=(f"Fall: person dropped {rate:.1f} bh/s and has been "
                         f"still for {still_span:.1f}s"),
                dedupe_key=f"fall:{cam_state.cam}:{track.id}",
                features={
                    "drop_rate": round(rate, 3),
                    "torso_angle": round(torso, 1),
                    "aspect": round(aspect, 3),
                    "still_s": round(still_span, 2),
                },
            ))
        return out


def _worst_drop(track: TrackState, now: float, height: float) -> tuple[float, float | None]:
    """Fastest downward hip movement between consecutive samples in the window.

    Consecutive pairs rather than the whole span, so a slow sit followed by a
    slow stand does not average out into something that looks like a collapse.
    """
    samples = track.samples_since(now - DROP_WINDOW_S)
    if len(samples) < 2 or height <= 1e-6:
        return 0.0, None
    best_rate, best_at = 0.0, None
    for older, newer in zip(samples, samples[1:]):
        dt = newer.t - older.t
        if dt <= 0:
            continue
        rate = (newer.hip()[1] - older.hip()[1]) / (height * dt)
        if rate > best_rate:
            best_rate, best_at = rate, newer.t
    return best_rate, best_at


def _stillness(track: TrackState, since: float) -> tuple[float, float]:
    """(seconds spanned, largest centroid displacement) among samples after `since`."""
    samples = track.samples_since(since)
    if len(samples) < 2:
        return 0.0, 0.0
    span = samples[-1].t - samples[0].t
    return span, track.displacement_since(since)


register(FallRule())
