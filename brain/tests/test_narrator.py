"""Narration is a bonus. These tests are mostly about it failing gracefully.

Every path through here can fail in normal operation: a browser node cannot take
a snapshot, Ollama is optional and often absent, the model may not be pulled. In
all of those cases the alert must survive with `narration: null`.
"""
from __future__ import annotations

from typing import Any

import pytest
import requests

from brain.alerts import AlertManager
from brain.config import ConfigStore
from brain.db import Database
from brain.narrator import Narrator
from brain.rules.base import Candidate
from brain.state import World
from brain.telegram import Telegram
from brain.tests.conftest import T0

JPEG = b"\xff\xd8\xff\xe0 pretend this is a pixelated jpeg \xff\xd9"
B64_JPEG = "/9j/4AAQIHByZXRlbmQ="


class FakeBus:
    """Stands in for MqttBus. `reply` is what a snapshot request returns."""

    def __init__(self, reply: dict[str, Any] | None = None) -> None:
        self.reply = reply
        self.requests: list[tuple[str, str]] = []
        self.published: list[tuple[str, dict[str, Any]]] = []

    def request(self, cam: str, cmd_type: str, *, timeout: float = 3.0, **_: Any):
        self.requests.append((cam, cmd_type))
        return self.reply

    def publish(self, topic: str, payload: dict[str, Any], *, retain: bool = False) -> int:
        self.published.append((topic, payload))
        return 0


class FakeResponse:
    def __init__(self, status_code: int, payload: Any = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


@pytest.fixture
def parts(tmp_path):
    db = Database(tmp_path / "t.db")
    bus = FakeBus()
    alerts = AlertManager(db, bus, ConfigStore())
    telegram = Telegram(token="", chat_id="")
    narrator = Narrator(bus, World(), alerts, telegram, snapshot_dir=tmp_path / "snaps")
    return narrator, alerts, bus, db


def _make_alert(alerts: AlertManager) -> dict[str, Any]:
    candidate = Candidate(
        type="fall", severity="critical", cam="cam-01",
        message="Fall: person dropped 1.4 bh/s and has been still for 1.6s",
        dedupe_key="fall:cam-01:17", track_ids=[17],
    )
    alert = alerts.create_or_update(candidate, {"cooldown_s": 30}, now=T0)
    assert alert is not None
    return alert


# --- snapshot failures ---------------------------------------------------
def test_a_snapshot_timeout_leaves_the_alert_unnarrated(parts):
    """A browser node cannot answer a snapshot request at all (§6)."""
    narrator, alerts, bus, db = parts
    bus.reply = None  # the request timed out
    alert = _make_alert(alerts)

    assert narrator.narrate(alert["id"], "cam-01") is None
    assert db.get_alert(alert["id"])["narration"] is None
    assert db.get_alert(alert["id"])["status"] == "open", "the alert must survive"
    assert narrator.no_snapshot == 1


def test_a_reply_without_an_image_is_handled(parts):
    narrator, alerts, bus, _db = parts
    bus.reply = {"req_id": "r_1"}
    assert narrator.narrate(_make_alert(alerts)["id"], "cam-01") is None


def test_corrupt_base64_does_not_raise(parts):
    narrator, alerts, bus, _db = parts
    bus.reply = {"req_id": "r_1", "jpg_b64": "this is not base64!!!"}
    assert narrator.narrate(_make_alert(alerts)["id"], "cam-01") is None


def test_narrating_an_alert_that_no_longer_exists_is_safe(parts):
    narrator, _alerts, bus, _db = parts
    bus.reply = {"req_id": "r_1", "jpg_b64": B64_JPEG}
    assert narrator.narrate("a_doesnotexist", "cam-01") is None


# --- ollama failures -----------------------------------------------------
def test_ollama_unreachable_does_not_raise(parts, monkeypatch):
    """Ollama is optional; most demo machines will not be running it."""
    narrator, alerts, bus, db = parts
    bus.reply = {"req_id": "r_1", "jpg_b64": B64_JPEG}

    def refuse(*_a, **_k):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr("brain.narrator.requests.post", refuse)
    alert = _make_alert(alerts)
    assert narrator.narrate(alert["id"], "cam-01") is None
    assert db.get_alert(alert["id"])["narration"] is None


def test_ollama_503_does_not_raise(parts, monkeypatch):
    narrator, alerts, bus, db = parts
    bus.reply = {"req_id": "r_1", "jpg_b64": B64_JPEG}
    monkeypatch.setattr(
        "brain.narrator.requests.post",
        lambda *_a, **_k: FakeResponse(503, text="model is loading"),
    )
    alert = _make_alert(alerts)
    assert narrator.narrate(alert["id"], "cam-01") is None
    assert db.get_alert(alert["id"])["narration"] is None


def test_ollama_404_for_a_missing_model_does_not_raise(parts, monkeypatch):
    narrator, alerts, bus, _db = parts
    bus.reply = {"req_id": "r_1", "jpg_b64": B64_JPEG}
    monkeypatch.setattr(
        "brain.narrator.requests.post",
        lambda *_a, **_k: FakeResponse(404, text='model "moondream" not found'),
    )
    assert narrator.narrate(_make_alert(alerts)["id"], "cam-01") is None


def test_ollama_timeout_does_not_raise(parts, monkeypatch):
    narrator, alerts, bus, _db = parts
    bus.reply = {"req_id": "r_1", "jpg_b64": B64_JPEG}

    def slow(*_a, **_k):
        raise requests.Timeout("took too long")

    monkeypatch.setattr("brain.narrator.requests.post", slow)
    assert narrator.narrate(_make_alert(alerts)["id"], "cam-01") is None


def test_a_non_json_body_does_not_raise(parts, monkeypatch):
    narrator, alerts, bus, _db = parts
    bus.reply = {"req_id": "r_1", "jpg_b64": B64_JPEG}
    monkeypatch.setattr(
        "brain.narrator.requests.post", lambda *_a, **_k: FakeResponse(200, None, "<html>")
    )
    assert narrator.narrate(_make_alert(alerts)["id"], "cam-01") is None


def test_complaints_are_rate_limited(parts, monkeypatch):
    """Without this, every alert logs the same "ollama is down" line."""
    narrator, alerts, bus, _db = parts
    bus.reply = {"req_id": "r_1", "jpg_b64": B64_JPEG}
    monkeypatch.setattr(
        "brain.narrator.requests.post", lambda *_a, **_k: FakeResponse(503, text="down")
    )
    warnings: list[str] = []
    monkeypatch.setattr(
        "brain.narrator.log.warning", lambda msg, *a: warnings.append(str(msg) % a if a else str(msg))
    )
    alert = _make_alert(alerts)
    for _ in range(5):
        narrator.narrate(alert["id"], "cam-01")
    assert len(warnings) == 1, f"complained {len(warnings)} times"
    assert narrator.no_model == 5


# --- the happy path ------------------------------------------------------
def test_a_successful_narration_is_stored_and_republished(parts, monkeypatch):
    narrator, alerts, bus, db = parts
    bus.reply = {"req_id": "r_1", "jpg_b64": B64_JPEG}
    monkeypatch.setattr(
        "brain.narrator.requests.post",
        lambda *_a, **_k: FakeResponse(
            200, {"response": "A person is lying on the floor near the far doorway."}
        ),
    )
    alert = _make_alert(alerts)
    bus.published.clear()

    result = narrator.narrate(alert["id"], "cam-01")
    assert result == "A person is lying on the floor near the far doorway."
    assert db.get_alert(alert["id"])["narration"] == result
    assert [t for t, _p in bus.published] == ["ghost/alerts"]
    assert bus.published[0][1]["narration"] == result
    assert narrator.narrated == 1


def test_only_the_first_sentence_is_kept(parts, monkeypatch):
    """Models volunteer paragraphs; a guard wants one line."""
    narrator, alerts, bus, _db = parts
    bus.reply = {"req_id": "r_1", "jpg_b64": B64_JPEG}
    monkeypatch.setattr(
        "brain.narrator.requests.post",
        lambda *_a, **_k: FakeResponse(
            200, {"response": "Someone has fallen.\nThey are not moving.\nNobody else is near."}
        ),
    )
    assert narrator.narrate(_make_alert(alerts)["id"], "cam-01") == "Someone has fallen."


def test_an_empty_response_is_treated_as_no_narration(parts, monkeypatch):
    narrator, alerts, bus, _db = parts
    bus.reply = {"req_id": "r_1", "jpg_b64": B64_JPEG}
    monkeypatch.setattr(
        "brain.narrator.requests.post", lambda *_a, **_k: FakeResponse(200, {"response": "   "})
    )
    assert narrator.narrate(_make_alert(alerts)["id"], "cam-01") is None


def test_the_snapshot_is_written_where_the_api_serves_it_from(parts, monkeypatch):
    narrator, alerts, bus, _db = parts
    # The brain serves this path at GET /api/alerts/{id}/snapshot.
    bus.reply = {"req_id": "r_1", "jpg_b64": B64_JPEG}
    monkeypatch.setattr(
        "brain.narrator.requests.post",
        lambda *_a, **_k: FakeResponse(200, {"response": "ok"}),
    )
    alert = _make_alert(alerts)
    narrator.narrate(alert["id"], "cam-01")

    path = narrator.snapshot_dir / f"{alert['id']}.jpg"
    assert path.exists() and path.read_bytes()


def test_the_prompt_carries_the_sensor_context(parts, monkeypatch):
    """The model is told what the rule measured, so it can agree or disagree."""
    narrator, alerts, bus, _db = parts
    bus.reply = {"req_id": "r_1", "jpg_b64": B64_JPEG}
    captured: dict[str, Any] = {}

    def capture(_url, json=None, timeout=None):  # noqa: A002
        captured.update(json or {})
        return FakeResponse(200, {"response": "ok"})

    monkeypatch.setattr("brain.narrator.requests.post", capture)
    narrator.narrate(_make_alert(alerts)["id"], "cam-01")

    assert "dropped 1.4 bh/s" in captured["prompt"]
    assert "do not mention that" in captured["prompt"].lower()
    assert captured["stream"] is False
    assert len(captured["images"]) == 1


# --- the pipeline keeps working -----------------------------------------
def test_alerts_still_fire_when_narration_blows_up(parts, monkeypatch):
    """The whole point: a broken narrator must not cost anyone an alert."""
    narrator, alerts, _bus, db = parts

    def explode(_alert: dict[str, Any]) -> None:
        raise RuntimeError("narrator is on fire")

    alerts.narrate = explode
    alert = _make_alert(alerts)
    assert alert is not None
    assert db.get_alert(alert["id"]) is not None
    assert alerts.created == 1


def test_enqueue_never_blocks_or_raises(parts):
    narrator, alerts, _bus, _db = parts
    alert = _make_alert(alerts)
    for _ in range(200):  # queue holds 64
        narrator.enqueue(alert)
    assert narrator.queue.full()


# --- telegram ------------------------------------------------------------
def test_telegram_is_a_no_op_without_configuration():
    t = Telegram(token="", chat_id="")
    assert t.enabled is False
    t.send_alert({"id": "a_1", "severity": "high", "type": "fall",
                  "message": "x", "cam": "cam-01"})
    assert t.sent == 0 and t.failed == 0


def test_telegram_escapes_html_in_messages():
    """An alert message is built from config, which a person edits."""
    t = Telegram(token="T", chat_id="1", dashboard_url="http://x:3000")
    text = t.format_alert({
        "id": "a_1", "severity": "critical", "type": "fall",
        "message": "<script>alert(1)</script> & more", "cam": "cam-01",
    }, "Corridor B")
    assert "<script>" not in text
    assert "&lt;script&gt;" in text and "&amp; more" in text
    assert '<a href="http://x:3000/alerts?id=a_1">' in text


def test_telegram_send_failures_are_swallowed(monkeypatch):
    t = Telegram(token="T", chat_id="1")

    def refuse(*_a, **_k):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr("brain.telegram.requests.post", refuse)
    t._post("hello")
    assert t.failed == 1 and t.sent == 0
