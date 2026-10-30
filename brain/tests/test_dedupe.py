"""De-duplication and cooldown: the difference between an alert and an alarm.

A person on the floor satisfies the fall rule ten times a second. A guard gets
told once, with a count that says it is still true.
"""
from __future__ import annotations

from typing import Any

import pytest

from brain.alerts import AlertManager
from brain.config import ConfigStore
from brain.constants import DEDUPE_WINDOW_S
from brain.db import Database
from brain.rules.base import Candidate
from brain.tests.conftest import T0


class FakeBus:
    """Records what would have gone out on ghost/alerts."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    def publish(self, topic: str, payload: dict[str, Any], *, retain: bool = False) -> int:
        self.published.append((topic, payload))
        return len(str(payload))


@pytest.fixture
def manager(tmp_path) -> AlertManager:
    return AlertManager(Database(tmp_path / "t.db"), FakeBus(), ConfigStore())


def _candidate(**overrides: Any) -> Candidate:
    base = {
        "type": "fall", "severity": "critical", "cam": "cam-01",
        "message": "Fall: person dropped 1.4 bh/s and has been still for 1.6s",
        "dedupe_key": "fall:cam-01:17", "track_ids": [17],
        "features": {"drop_rate": 1.4},
    }
    base.update(overrides)
    return Candidate(**base)


RULE = {"id": "fall-all", "severity": "critical", "cooldown_s": 30.0}


def test_first_candidate_creates_an_alert(manager: AlertManager):
    alert = manager.create_or_update(_candidate(), RULE, now=T0)
    assert alert is not None
    assert alert["id"].startswith("a_")
    assert alert["status"] == "open"
    assert alert["count"] == 1
    assert manager.created == 1


def test_a_repeat_updates_the_same_alert_rather_than_creating_another(manager: AlertManager):
    first = manager.create_or_update(_candidate(), RULE, now=T0)
    second = manager.create_or_update(_candidate(), RULE, now=T0 + 0.1)
    assert second is not None
    assert second["id"] == first["id"], "the dashboard must see one alert changing"
    assert second["count"] == 2
    assert manager.created == 1 and manager.updated == 1


def test_count_climbs_while_the_condition_persists(manager: AlertManager):
    for i in range(10):
        manager.create_or_update(_candidate(), RULE, now=T0 + i * 0.1)
    alert = manager.db.list_alerts()[0]
    assert alert["count"] == 10
    assert len(manager.db.list_alerts()) == 1


def test_last_seen_advances_with_each_repeat(manager: AlertManager):
    manager.create_or_update(_candidate(), RULE, now=T0)
    manager.create_or_update(_candidate(), RULE, now=T0 + 5.0)
    alert = manager.db.list_alerts()[0]
    assert alert["last_seen"] == pytest.approx(T0 + 5.0)
    assert alert["ts"] == pytest.approx(T0)


def test_features_refresh_on_update(manager: AlertManager):
    manager.create_or_update(_candidate(), RULE, now=T0)
    manager.create_or_update(
        _candidate(features={"drop_rate": 2.9}), RULE, now=T0 + 1.0
    )
    assert manager.db.list_alerts()[0]["features"]["drop_rate"] == 2.9


def test_cooldown_suppresses_a_refire_after_the_alert_is_resolved(manager: AlertManager):
    first = manager.create_or_update(_candidate(), RULE, now=T0)
    assert first is not None
    manager.db.update_alert(first["id"], status="resolved")

    # Same key, well inside cooldown_s: nothing at all should happen.
    assert manager.create_or_update(_candidate(), RULE, now=T0 + 10.0) is None
    assert manager.suppressed == 1
    assert len(manager.db.list_alerts()) == 1


def test_a_new_alert_is_allowed_once_the_cooldown_expires(manager: AlertManager):
    first = manager.create_or_update(_candidate(), RULE, now=T0)
    assert first is not None
    manager.db.update_alert(first["id"], status="resolved")
    second = manager.create_or_update(_candidate(), RULE, now=T0 + 31.0)
    assert second is not None and second["id"] != first["id"]
    assert len(manager.db.list_alerts()) == 2


def test_an_alert_older_than_the_dedupe_window_is_not_updated(manager: AlertManager):
    """After a minute of silence the next occurrence is a new event."""
    first = manager.create_or_update(_candidate(), RULE, now=T0)
    assert first is not None
    later = manager.create_or_update(_candidate(), RULE, now=T0 + DEDUPE_WINDOW_S + 1.0)
    assert later is not None and later["id"] != first["id"]


def test_an_acked_alert_still_absorbs_repeats(manager: AlertManager):
    """Acknowledging means "I know", not "tell me again"."""
    first = manager.create_or_update(_candidate(), RULE, now=T0)
    assert first is not None
    manager.db.update_alert(first["id"], status="acked")
    second = manager.create_or_update(_candidate(), RULE, now=T0 + 1.0)
    assert second is not None and second["id"] == first["id"]
    assert second["count"] == 2


def test_different_dedupe_keys_are_different_alerts(manager: AlertManager):
    manager.create_or_update(_candidate(dedupe_key="fall:cam-01:17"), RULE, now=T0)
    manager.create_or_update(_candidate(dedupe_key="fall:cam-01:18"), RULE, now=T0)
    manager.create_or_update(_candidate(dedupe_key="fall:cam-02:17"), RULE, now=T0)
    assert len(manager.db.list_alerts()) == 3


def test_every_create_and_update_is_published(manager: AlertManager):
    manager.create_or_update(_candidate(), RULE, now=T0)
    manager.create_or_update(_candidate(), RULE, now=T0 + 0.5)
    topics = [t for t, _p in manager.bus.published]
    assert topics == ["ghost/alerts", "ghost/alerts"]
    payload = manager.bus.published[-1][1]
    assert payload["cam_name"] == "Corridor B"
    assert payload["colour"] == "#ff3b3b"


def test_suppressed_candidates_are_not_published(manager: AlertManager):
    first = manager.create_or_update(_candidate(), RULE, now=T0)
    assert first is not None
    manager.db.update_alert(first["id"], status="resolved")
    manager.bus.published.clear()
    manager.create_or_update(_candidate(), RULE, now=T0 + 5.0)
    assert manager.bus.published == []


# --- side effects -------------------------------------------------------
def test_side_effects_fire_once_on_creation_only(manager: AlertManager):
    calls: list[str] = []
    manager.notify = lambda a: calls.append("notify")
    manager.narrate = lambda a: calls.append("narrate")
    manager.seal = lambda a: calls.append("seal")

    manager.create_or_update(_candidate(), RULE, now=T0)
    manager.create_or_update(_candidate(), RULE, now=T0 + 0.5)
    assert calls == ["notify", "narrate", "seal"], "repeats must not re-notify or re-seal"


def test_auto_seal_respects_the_severity_floor(manager: AlertManager):
    """AUTO_SEAL_MIN_SEVERITY is high by default, so a low alert is not sealed."""
    sealed: list[str] = []
    manager.seal = lambda a: sealed.append(a["severity"])
    manager.create_or_update(
        _candidate(severity="low", dedupe_key="running:cam-01:1"), RULE, now=T0
    )
    manager.create_or_update(
        _candidate(severity="critical", dedupe_key="fall:cam-01:1"), RULE, now=T0
    )
    assert sealed == ["critical"]


def test_a_failing_side_effect_never_loses_the_alert(manager: AlertManager):
    """A dead Ollama or an unreachable Telegram must not cost a guard an alert."""
    def explode(_alert: dict[str, Any]) -> None:
        raise RuntimeError("ollama is on fire")

    manager.notify = explode
    manager.narrate = explode
    manager.seal = explode
    alert = manager.create_or_update(_candidate(), RULE, now=T0)
    assert alert is not None
    assert manager.db.get_alert(alert["id"]) is not None
