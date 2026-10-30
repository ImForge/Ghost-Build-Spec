"""Alert creation, de-duplication, and twin publishing.

Twin publishing lives here because it is the other thing the brain pushes to the
dashboard on every event, and it shares the floor-mapping code with the alert
positions.

Alert creation itself (`create_or_update`) arrives with the rule engine.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from brain import geometry as geo
from brain.constants import topic_twin

if TYPE_CHECKING:  # pragma: no cover
    from brain.config import ConfigStore
    from brain.mqtt_bus import MqttBus
    from brain.state import CamState

log = logging.getLogger("brain.alerts")


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
    payload = twin_payload(cam_state, config.cam(cam_state.cam))
    bus.publish(topic_twin(cam_state.cam), payload)
