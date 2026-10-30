"""Loitering: someone who has been in one zone far longer than passing through.

The dwell clock lives in `zone_enter`, which state.py only clears after the track
has been continuously outside the zone for a couple of seconds. That grace period
is the whole trick: trackers drop people behind pillars and pick them up again,
and without it a loiterer could reset their own timer by standing still badly.

The seeded dwell is 20 s so the demo fires while someone is watching. Production
wants 300.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from brain.rules import register
from brain.rules.base import Candidate, Rule

if TYPE_CHECKING:  # pragma: no cover
    from brain.config import ConfigStore
    from brain.state import CamState

log = logging.getLogger("brain.rules.loiter")


class LoiterRule(Rule):
    type = "loiter"

    def evaluate(self, cam_state: CamState, rule_cfg: dict[str, Any], now: float,
                 config: ConfigStore) -> list[Candidate]:
        zone = self.zone_cfg(config, rule_cfg)
        if zone is None:
            return []
        zone_id = str(zone["id"])
        dwell_min = float(self.param(rule_cfg, "dwell_s", 300))

        out: list[Candidate] = []
        for track in list(cam_state.tracks.values()):
            entered = track.zone_enter.get(zone_id)
            if entered is None or zone_id not in track.in_zones:
                continue
            dwell = now - entered
            if dwell < dwell_min:
                continue
            out.append(Candidate(
                type=self.type,
                severity=str(rule_cfg.get("severity", "low")),
                cam=cam_state.cam,
                zone=zone_id,
                track_ids=[track.id],
                message=(f"Loitering in {zone.get('name', zone_id)}: "
                         f"{dwell / 60:.1f} min in the same area"
                         if dwell >= 120 else
                         f"Loitering in {zone.get('name', zone_id)}: {dwell:.0f}s "
                         f"in the same area"),
                dedupe_key=f"loiter:{cam_state.cam}:{zone_id}:{track.id}",
                features={"dwell_s": round(dwell, 1), "dwell_limit_s": dwell_min},
            ))
        return out


register(LoiterRule())
