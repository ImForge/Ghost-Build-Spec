"""Alert creation, de-duplication and twin publishing.

De-duplication is what makes the system usable rather than merely correct. A
person lying on the floor satisfies the fall rule ten times a second; a guard
needs to be told once. Three things happen in order:

1. an open alert with the same dedupe_key seen in the last minute is *updated* —
   its count goes up and its features refresh, and the dashboard sees the same
   alert id change rather than a new row;
2. otherwise, if that dedupe_key fired within its rule's cooldown, nothing
   happens at all;
3. otherwise it is a new alert, and that is when the side effects fire —
   Telegram, narration, and automatic evidence sealing above a severity floor.

Only step 3 has side effects, and none of them can block alert creation: a dead
Ollama or an unreachable Telegram must never cost a guard an alert.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Callable

from brain import geometry as geo
from brain.constants import (
    DEDUPE_WINDOW_S, SEVERITY_COLOURS, TOPIC_ALERTS, severity_at_least, topic_twin,
)
from brain.db import new_id
from brain.settings import settings

if TYPE_CHECKING:  # pragma: no cover
    from brain.config import ConfigStore
    from brain.db import Database
    from brain.mqtt_bus import MqttBus
    from brain.rules.base import Candidate
    from brain.state import CamState

log = logging.getLogger("brain.alerts")


# --- twin ---------------------------------------------------------------
def twin_payload(cam_state: CamState, cam_cfg: dict[str, Any] | None) -> dict[str, Any]:
    """The §3.6 twin message: where each tracked person is on the floor plan."""
    homography = (cam_cfg or {}).get("homography")
    default_rect = (cam_cfg or {}).get("default_rect")
    people = []
    for track in cam_state.tracks.values():
        sample = track.latest
        if sample is None:
            continue
        x, y = geo.to_floor(sample.foot, homography, default_rect)
        people.append({"id": track.id, "x": round(x, 1), "y": round(y, 1)})
    return {
        "cam": cam_state.cam,
        "ts": round(cam_state.last_event, 3),
        "calibrated": homography is not None,
        "people": people,
    }


def publish_twin(bus: MqttBus, config: ConfigStore, cam_state: CamState) -> None:
    """Push floor-plan positions for one camera. Called on every skeleton event."""
    bus.publish(topic_twin(cam_state.cam), twin_payload(cam_state, config.cam(cam_state.cam)))


# --- alerts -------------------------------------------------------------
class AlertManager:
    """Turns rule candidates into alerts, exactly once each.

    `notify`, `narrate` and `seal` are attached by later wiring (Telegram, the
    narrator worker, the escrow service). While they are None the pipeline still
    works end to end — it just stays quiet.
    """

    def __init__(self, db: Database, bus: MqttBus, config: ConfigStore) -> None:
        self.db = db
        self.bus = bus
        self.config = config
        self.notify: Callable[[dict[str, Any]], None] | None = None
        self.narrate: Callable[[dict[str, Any]], None] | None = None
        self.seal: Callable[[dict[str, Any]], None] | None = None
        self.created = 0
        self.updated = 0
        self.suppressed = 0

    def create_or_update(self, candidate: Candidate, rule_cfg: dict[str, Any],
                         now: float | None = None) -> dict[str, Any] | None:
        """Returns the alert if one was created or updated, else None."""
        now = time.time() if now is None else now
        cooldown = float(rule_cfg.get("cooldown_s", 30))

        existing = self.db.find_open_by_dedupe(candidate.dedupe_key, now - DEDUPE_WINDOW_S)
        if existing is not None:
            return self._update(existing, candidate, now)

        last = self.db.last_fired_at(candidate.dedupe_key)
        if last and now - last < cooldown:
            self.suppressed += 1
            return None

        return self._create(candidate, rule_cfg, now)

    def _update(self, existing: dict[str, Any], candidate: Candidate,
                now: float) -> dict[str, Any]:
        count = int(existing.get("count", 1)) + 1
        self.db.update_alert(
            existing["id"], count=count, last_seen=now,
            features=candidate.features, message=candidate.message,
        )
        alert = self.db.get_alert(existing["id"]) or existing
        self.updated += 1
        self.bus.publish(TOPIC_ALERTS, self._wire(alert))
        return alert

    def _create(self, candidate: Candidate, rule_cfg: dict[str, Any],
                now: float) -> dict[str, Any]:
        alert = {
            "id": new_id("a"),
            "ts": now,
            "last_seen": now,
            "cam": candidate.cam,
            "type": candidate.type,
            "severity": candidate.severity,
            "zone": candidate.zone,
            "line": candidate.line,
            "track_ids": list(candidate.track_ids),
            "message": candidate.message,
            "narration": None,
            "status": "open",
            "count": 1,
            "evidence_id": None,
            "features": dict(candidate.features),
            "dedupe_key": candidate.dedupe_key,
        }
        self.db.insert_alert(alert)
        self.created += 1
        log.info("[%s] %s %s: %s", alert["cam"], alert["severity"], alert["type"],
                 alert["message"])
        self.bus.publish(TOPIC_ALERTS, self._wire(alert))

        # Side effects, each isolated: a failure here must not lose the alert.
        self._fire(self.notify, alert, "telegram")
        self._fire(self.narrate, alert, "narration")
        if severity_at_least(alert["severity"], settings.auto_seal_min_severity):
            self._fire(self.seal, alert, "seal")
        return alert

    @staticmethod
    def _fire(hook: Callable[[dict[str, Any]], None] | None, alert: dict[str, Any],
              what: str) -> None:
        if hook is None:
            return
        try:
            hook(alert)
        except Exception:  # noqa: BLE001 — never let a side effect lose an alert
            log.exception("%s hook failed for alert %s", what, alert["id"])

    def republish(self, alert_id: str) -> dict[str, Any] | None:
        alert = self.db.get_alert(alert_id)
        if alert is not None:
            self.bus.publish(TOPIC_ALERTS, self._wire(alert))
        return alert

    def _wire(self, alert: dict[str, Any]) -> dict[str, Any]:
        """The §3.7 alert, plus the bits the dashboard would otherwise look up."""
        return {
            **alert,
            "cam_name": self.config.cam_name(str(alert["cam"])),
            "colour": SEVERITY_COLOURS.get(str(alert["severity"])),
        }
