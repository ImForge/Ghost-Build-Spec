"""Unauthorized entry into a restricted zone.

Fires on the transition, not on presence: standing inside a server room for an
hour is one alert, not thirty-six thousand. The rule reads `zone_enter`, which
state.py sets the moment a track's feet land inside and only clears after a
continuous spell outside, so a one-frame detection gap does not re-fire.

Time windows are the rule's own (`hours` in rules.yaml) and are applied by the
engine before this is ever called — which is what makes "nobody in the lab after
8pm" a configuration change rather than a code change.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from brain.rules import register
from brain.rules.base import Candidate, Rule

if TYPE_CHECKING:  # pragma: no cover
    from brain.config import ConfigStore
    from brain.state import CamState

log = logging.getLogger("brain.rules.entry")


class EntryRule(Rule):
    type = "entry"

    def evaluate(self, cam_state: CamState, rule_cfg: dict[str, Any], now: float,
                 config: ConfigStore) -> list[Candidate]:
        zone = self.zone_cfg(config, rule_cfg)
        if zone is None:
            return []
        zone_id = str(zone["id"])
        zone_name = str(zone.get("name", zone_id))

        out: list[Candidate] = []
        for track in list(cam_state.tracks.values()):
            if zone_id not in track.in_zones:
                continue
            entered = track.zone_enter.get(zone_id)
            if entered is None:
                continue
            # Keyed on the entry timestamp, so staying inside raises nothing more
            # but leaving and coming back raises a fresh alert.
            if cam_state.already_reported(f"entry:{zone_id}:{track.id}", entered):
                continue
            out.append(Candidate(
                type=self.type,
                severity=str(rule_cfg.get("severity", "high")),
                cam=cam_state.cam,
                zone=zone_id,
                track_ids=[track.id],
                message=f"Unauthorized entry into {zone_name}",
                dedupe_key=f"entry:{cam_state.cam}:{zone_id}:{track.id}",
                features={"entered_ago_s": round(now - entered, 2)},
            ))
        return out


register(EntryRule())
