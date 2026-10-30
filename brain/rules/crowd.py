"""Crowding and crush risk.

Crowd watches one zone three ways: how many people are in it, how densely they
are packed, and how fast that number is climbing. The rate test is the one that
matters for safety — fifteen people standing in a gate is a queue, but five
people arriving in ten seconds is something happening.

Crush is the harder claim, so it is only made on a calibrated camera: without a
homography there is no way to turn pixels into metres and "half a metre apart"
would be meaningless.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from brain import geometry as geo
from brain.rules import register
from brain.rules.base import Candidate, Rule

if TYPE_CHECKING:  # pragma: no cover
    from brain.config import ConfigStore
    from brain.state import CamState

log = logging.getLogger("brain.rules.crowd")

RATE_WINDOW_S = 10.0


class CrowdRule(Rule):
    type = "crowd"

    def evaluate(self, cam_state: CamState, rule_cfg: dict[str, Any], now: float,
                 config: ConfigStore) -> list[Candidate]:
        zone = self.zone_cfg(config, rule_cfg)
        if zone is None:
            return []
        zone_id = str(zone["id"])

        inside = [t for t in cam_state.tracks.values() if zone_id in t.in_zones]
        count = len(inside)
        cam_state.record_count(zone_id, now, count)

        max_count = float(self.param(rule_cfg, "max_count", 15))
        max_density = float(self.param(rule_cfg, "max_density", 2.5))
        max_rate = float(self.param(rule_cfg, "rate_per_s", 0.5))

        area = float(zone.get("area_m2") or 0.0)
        density = count / area if area > 0 else 0.0

        past = cam_state.count_at(zone_id, now - RATE_WINDOW_S)
        rate = (count - past) / RATE_WINDOW_S if past is not None else 0.0

        reasons = []
        if count >= max_count:
            reasons.append(f"{count} people (limit {max_count:g})")
        if area > 0 and density >= max_density:
            reasons.append(f"{density:.1f} people/m² (limit {max_density:g})")
        if rate >= max_rate:
            reasons.append(f"filling at {rate:.1f} people/s (limit {max_rate:g})")
        if not reasons:
            return []

        return [Candidate(
            type=self.type,
            severity=str(rule_cfg.get("severity", "high")),
            cam=cam_state.cam,
            zone=zone_id,
            track_ids=sorted(t.id for t in inside),
            message=f"Crowding in {zone.get('name', zone_id)}: " + ", ".join(reasons),
            dedupe_key=f"crowd:{cam_state.cam}:{zone_id}",
            features={
                "count": count,
                "density": round(density, 3),
                "rate_per_s": round(rate, 3),
            },
        )]


class CrushRule(Rule):
    type = "crush"

    def evaluate(self, cam_state: CamState, rule_cfg: dict[str, Any], now: float,
                 config: ConfigStore) -> list[Candidate]:
        cam_cfg = config.cam(cam_state.cam) or {}
        homography = cam_cfg.get("homography")
        if homography is None:
            # Without calibration there is no metre scale, so there is no honest
            # way to say how close together these people are.
            return []

        min_people = int(self.param(rule_cfg, "min_people", 4))
        max_nn = float(self.param(rule_cfg, "max_nn_dist_m", 0.5))
        px_per_m = float(cam_cfg.get("px_per_m") or 40)

        points = []
        ids = []
        for track in cam_state.tracks.values():
            sample = track.latest
            if sample is None:
                continue
            points.append(geo.to_floor(sample.foot, homography, cam_cfg.get("default_rect")))
            ids.append(track.id)

        if len(points) < min_people:
            return []
        nn = geo.nn_mean_dist_m(points, px_per_m)
        if nn is None or nn > max_nn:
            return []

        return [Candidate(
            type=self.type,
            severity=str(rule_cfg.get("severity", "critical")),
            cam=cam_state.cam,
            track_ids=sorted(ids),
            message=(f"Crush risk on {config.cam_name(cam_state.cam)}: {len(points)} people "
                     f"averaging {nn:.2f} m apart (limit {max_nn:g} m)"),
            dedupe_key=f"crush:{cam_state.cam}",
            features={"people": len(points), "nn_dist_m": round(nn, 3)},
        )]


register(CrowdRule())
register(CrushRule())
