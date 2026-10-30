"""Line crossings and tailgating.

A crossing needs both the side sign to flip and the walk segment to actually
intersect the drawn segment (brain/geometry.py). Without the second test,
walking past the end of a doorway registers as walking through it.

Tailgating is the more interesting one: two *distinct* people through one door
inside a couple of seconds is one badge opening the door for two. The distinctness
matters — a tracker that briefly loses and re-acquires the same person would
otherwise report every entry as a tailgate.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from brain.rules import register
from brain.rules.base import Candidate, Rule

if TYPE_CHECKING:  # pragma: no cover
    from brain.config import ConfigStore
    from brain.state import CamState

log = logging.getLogger("brain.rules.tripwire")

# How far back to look for crossings not yet reported. Generous on purpose: what
# stops a crossing being reported twice is the per-line mark, not this window.
LOOKBACK_S = 5.0


class TripwireRule(Rule):
    type = "tripwire"

    def evaluate(self, cam_state: CamState, rule_cfg: dict[str, Any], now: float,
                 config: ConfigStore) -> list[Candidate]:
        line = self.line_cfg(config, rule_cfg)
        if line is None:
            return []
        line_id = str(line["id"])
        wanted = str(line.get("direction", "any"))

        out: list[Candidate] = []
        for ts, track_id, direction in cam_state.recent_crossings(line_id, LOOKBACK_S, now):
            if wanted == "pos" and direction != 1:
                continue
            if wanted == "neg" and direction != -1:
                continue
            if cam_state.already_reported(f"tripwire:{line_id}:{track_id}", ts):
                continue
            way = {1: "inward", -1: "outward"}.get(direction, "across")
            out.append(Candidate(
                type=self.type,
                severity=str(rule_cfg.get("severity", "medium")),
                cam=cam_state.cam,
                line=line_id,
                track_ids=[track_id],
                message=f"Crossing at {line.get('name', line_id)} ({way})",
                dedupe_key=f"tripwire:{cam_state.cam}:{line_id}:{track_id}",
                features={"direction": direction, "crossed_ago_s": round(now - ts, 2)},
            ))
        return out


class TailgateRule(Rule):
    type = "tailgate"

    def evaluate(self, cam_state: CamState, rule_cfg: dict[str, Any], now: float,
                 config: ConfigStore) -> list[Candidate]:
        line = self.line_cfg(config, rule_cfg)
        if line is None:
            return []
        line_id = str(line["id"])
        window_s = float(self.param(rule_cfg, "window_s", 2.0))

        recent = cam_state.recent_crossings(line_id, window_s, now)
        distinct = {track_id for _ts, track_id, _d in recent}
        if len(distinct) < 2:
            return []
        # One burst of crossings is one tailgating event, however long it stays
        # inside the window.
        newest = max(ts for ts, _t, _d in recent)
        if cam_state.already_reported(f"tailgate:{line_id}", newest):
            return []

        return [Candidate(
            type=self.type,
            severity=str(rule_cfg.get("severity", "medium")),
            cam=cam_state.cam,
            line=line_id,
            track_ids=sorted(distinct),
            message=(f"Tailgating at {line.get('name', line_id)}: "
                     f"{len(distinct)} people within {window_s:g}s"),
            dedupe_key=f"tailgate:{cam_state.cam}:{line_id}",
            features={"people": len(distinct), "window_s": window_s,
                      "crossings": len(recent)},
        )]


register(TripwireRule())
register(TailgateRule())
