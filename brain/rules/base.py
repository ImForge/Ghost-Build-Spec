"""What a rule is.

A rule reads camera state and returns candidates. It does not publish, write to
the database, send Telegram messages or seal evidence — the alert pipeline does
all of that. Keeping rules pure is what lets every one of them be tested by
handing it a synthetic track history and checking what comes back.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Sequence

if TYPE_CHECKING:  # pragma: no cover
    from brain.config import ConfigStore
    from brain.state import CamState


@dataclass
class Candidate:
    """A rule's finding, before de-duplication decides whether it is news."""

    type: str
    severity: str
    cam: str
    message: str
    dedupe_key: str
    zone: str | None = None
    line: str | None = None
    track_ids: list[int] = field(default_factory=list)
    features: dict[str, Any] = field(default_factory=dict)


class Rule:
    """Base class. Subclasses set `type` and implement `evaluate`."""

    type: str = ""

    def evaluate(self, cam_state: CamState, rule_cfg: dict[str, Any], now: float,
                 config: ConfigStore) -> list[Candidate]:
        raise NotImplementedError

    # --- helpers shared by rules -----------------------------------------
    @staticmethod
    def param(rule_cfg: dict[str, Any], name: str, default: Any = None) -> Any:
        return (rule_cfg.get("params") or {}).get(name, default)

    @staticmethod
    def zone_cfg(config: ConfigStore, rule_cfg: dict[str, Any]) -> dict[str, Any] | None:
        zone_id = rule_cfg.get("zone")
        return config.zone(str(zone_id)) if zone_id else None

    @staticmethod
    def line_cfg(config: ConfigStore, rule_cfg: dict[str, Any]) -> dict[str, Any] | None:
        line_id = rule_cfg.get("line")
        return config.line(str(line_id)) if line_id else None

    @staticmethod
    def in_furniture(config: ConfigStore, cam: str, foot: Sequence[float]) -> bool:
        """Is this foot position on a bench, a chair, a low wall?

        Sitting down looks a lot like falling over. Marking the furniture is the
        cheapest way to stop a bench generating a critical alert every lunchtime.
        """
        from brain import geometry as geo

        for zone in config.zones_for(cam, "furniture"):
            if geo.point_in_polygon(
                (float(foot[0]), float(foot[1])), str(zone["id"]), zone.get("polygon") or []
            ):
                return True
        return False
