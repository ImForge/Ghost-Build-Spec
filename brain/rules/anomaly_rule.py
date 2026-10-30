"""Learned anomalies.

This rule does no learning itself: brain/anomaly.py scores a feature window every
five seconds and pushes a flag onto the camera's ring of recent verdicts. The
rule's only job is to insist on a run of consecutive flagged windows before
saying anything, because a single odd ten-second window is usually a tracker
glitch rather than an event.

With no trained model the flag ring stays empty and this never fires, which is
the correct behaviour on a camera nobody has taught yet.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from brain.rules import register
from brain.rules.base import Candidate, Rule

if TYPE_CHECKING:  # pragma: no cover
    from brain.config import ConfigStore
    from brain.state import CamState

log = logging.getLogger("brain.rules.anomaly")


class AnomalyRule(Rule):
    type = "anomaly"

    def evaluate(self, cam_state: CamState, rule_cfg: dict[str, Any], now: float,
                 config: ConfigStore) -> list[Candidate]:
        needed = int(self.param(rule_cfg, "consecutive_windows", 2))
        flags = list(cam_state.anomaly_flags)
        if len(flags) < needed or not all(flags[-needed:]):
            return []
        # One unusual window is one occurrence. The engine ticks at 10 Hz but
        # the score only changes every five seconds, so without this the alert's
        # count would climb fifty times faster than anything actually happened.
        if cam_state.already_reported(f"anomaly:{cam_state.cam}", cam_state.anomaly_at):
            return []

        explain = cam_state.anomaly_explain or []
        if explain:
            detail = ", ".join(
                f"{item['feature']} is {item['z']:+.1f}σ from normal" for item in explain[:2]
            )
        else:
            detail = f"{needed} consecutive unusual windows"

        return [Candidate(
            type=self.type,
            severity=str(rule_cfg.get("severity", "medium")),
            cam=cam_state.cam,
            message=f"Unusual activity on {config.cam_name(cam_state.cam)}: {detail}",
            dedupe_key=f"anomaly:{cam_state.cam}",
            features={"windows": needed,
                      "explain": explain[:2]},
        )]


register(AnomalyRule())
