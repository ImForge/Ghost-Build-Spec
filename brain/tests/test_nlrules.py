"""Turning a sentence into a rule — and refusing to trust the model that did it.

Ollama is optional and usually absent on a build machine, so the HTTP call is
mocked. What is tested for real is the part that matters: validation. A model
that invents a zone id must produce an error, not a rule that silently never
fires.
"""
from __future__ import annotations

from typing import Any

import pytest
import requests

from brain.config import ConfigStore
from brain.nlrules import NlRuleError, build_prompt, extract_json, parse, validate


@pytest.fixture
def config() -> ConfigStore:
    return ConfigStore()


class FakeResponse:
    def __init__(self, status_code: int = 200, response: str = "", payload: Any = ...) -> None:
        self.status_code = status_code
        self._payload = {"response": response} if payload is ... else payload
        self.text = response

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _reply(monkeypatch, body: str, status: int = 200) -> None:
    monkeypatch.setattr(
        "brain.nlrules.requests.post",
        lambda *_a, **_k: FakeResponse(status, body),
    )


# --- validation is the real subject --------------------------------------
def test_a_well_formed_rule_passes(config):
    rule = validate(config, {
        "type": "crowd", "cam": "cam-02", "zone": "gate-area", "line": None,
        "severity": "high", "cooldown_s": 60, "hours": None,
        "params": {"max_count": 10},
    })
    assert rule["type"] == "crowd"
    assert rule["zone"] == "gate-area"
    assert rule["params"]["max_count"] == 10
    assert rule["enabled"] is True


def test_an_invented_zone_is_rejected(config):
    """The headline failure mode: a hallucinated id installs a dead rule."""
    with pytest.raises(NlRuleError) as excinfo:
        validate(config, {"type": "crowd", "cam": "cam-01", "zone": "the-canteen"})
    assert "no zone called 'the-canteen'" in str(excinfo.value)
    assert "corridor-b" in str(excinfo.value), "the error should list what does exist"


def test_an_invented_camera_is_rejected(config):
    with pytest.raises(NlRuleError) as excinfo:
        validate(config, {"type": "fall", "cam": "cam-99"})
    assert "no camera called 'cam-99'" in str(excinfo.value)


def test_an_invented_line_is_rejected(config):
    with pytest.raises(NlRuleError) as excinfo:
        validate(config, {"type": "tripwire", "cam": "cam-01", "line": "side-gate"})
    assert "no line called 'side-gate'" in str(excinfo.value)


def test_an_unknown_rule_type_is_rejected(config):
    with pytest.raises(NlRuleError) as excinfo:
        validate(config, {"type": "telepathy", "cam": "*"})
    assert "not a rule type" in str(excinfo.value)


def test_a_star_camera_is_allowed(config):
    assert validate(config, {"type": "fall", "cam": "*"})["cam"] == "*"


def test_a_missing_camera_defaults_to_every_camera(config):
    assert validate(config, {"type": "fall"})["cam"] == "*"


def test_unknown_params_are_dropped_not_passed_through(config):
    """A model that invents a threshold name would otherwise have it ignored
    silently by the rule and look like it was applied."""
    rule = validate(config, {
        "type": "loiter", "cam": "cam-01", "zone": "corridor-b",
        "params": {"dwell_s": 120, "vibes": 9, "max_count": 3},
    })
    assert rule["params"]["dwell_s"] == 120
    assert "vibes" not in rule["params"]
    assert "max_count" not in rule["params"]


def test_missing_params_fall_back_to_the_type_defaults(config):
    rule = validate(config, {"type": "fall", "cam": "*", "params": {}})
    assert rule["params"]["torso_angle_deg"] == 60
    assert rule["params"]["still_s"] == 1.5


def test_an_unknown_severity_becomes_medium(config):
    assert validate(config, {"type": "fall", "cam": "*", "severity": "urgent"})[
        "severity"
    ] == "medium"


def test_hours_are_normalised(config):
    rule = validate(config, {
        "type": "entry", "cam": "cam-01", "zone": "server-room",
        "hours": [["8:00", "18:30"]],
    })
    assert rule["hours"] == [["08:00", "18:30"]]


def test_hours_that_wrap_midnight_are_kept(config):
    rule = validate(config, {
        "type": "entry", "cam": "cam-01", "zone": "server-room",
        "hours": [["20:00", "06:00"]],
    })
    assert rule["hours"] == [["20:00", "06:00"]]


@pytest.mark.parametrize("bad", [[["25:00", "06:00"]], [["8am", "6pm"]], [["08:00"]], "evenings"])
def test_malformed_hours_are_rejected(config, bad):
    with pytest.raises(NlRuleError):
        validate(config, {"type": "fall", "cam": "*", "hours": bad})


def test_a_non_numeric_threshold_is_dropped(config):
    rule = validate(config, {
        "type": "crowd", "cam": "cam-01", "zone": "corridor-b",
        "params": {"max_count": "lots"},
    })
    assert rule["params"]["max_count"] == 15  # the default, not "lots"


# --- getting JSON out of a chatty model ----------------------------------
def test_plain_json_parses():
    assert extract_json('{"type": "fall"}') == {"type": "fall"}


def test_a_code_fence_is_unwrapped():
    assert extract_json('```json\n{"type": "fall"}\n```') == {"type": "fall"}


def test_json_buried_in_prose_is_found():
    raw = 'Sure! Here is the rule:\n{"type": "fall", "cam": "*"}\nHope that helps.'
    assert extract_json(raw)["type"] == "fall"


def test_no_json_at_all_is_an_error():
    with pytest.raises(NlRuleError) as excinfo:
        extract_json("I'm afraid I can't help with that.")
    assert "did not return any JSON" in str(excinfo.value)


def test_a_json_array_is_not_a_rule():
    with pytest.raises(NlRuleError):
        extract_json('[{"type": "fall"}]')


# --- the prompt ----------------------------------------------------------
def test_the_prompt_lists_the_real_cameras_zones_and_lines(config):
    prompt = build_prompt(config, "alert me about the bench")
    for identifier in ("cam-01", "cam-02", "corridor-b", "server-room", "bench-1", "door-1"):
        assert identifier in prompt
    assert "Corridor B" in prompt
    assert "tailgate" in prompt


def test_the_prompt_carries_the_instruction(config):
    assert "people near the bench" in build_prompt(config, "people near the bench")


# --- the whole call ------------------------------------------------------
def test_parse_returns_a_rule_without_saving_it(config, monkeypatch):
    _reply(monkeypatch, '{"type":"crowd","cam":"cam-01","zone":"bench-1",'
                        '"severity":"medium","cooldown_s":90,"hours":[["18:00","23:59"]],'
                        '"params":{"max_count":4}}')
    rule = parse(config, "alert if more than 4 people near the bench after 6pm")
    assert rule["type"] == "crowd"
    assert rule["zone"] == "bench-1"
    assert rule["params"]["max_count"] == 4
    assert rule["hours"] == [["18:00", "23:59"]]
    # Nothing was written: the dashboard shows this for confirmation first.
    assert all(r["id"] != "crowd-bench-1" for r in config.rules)


def test_ollama_unreachable_is_a_503(config, monkeypatch):
    def refuse(*_a, **_k):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr("brain.nlrules.requests.post", refuse)
    with pytest.raises(NlRuleError) as excinfo:
        parse(config, "anything")
    assert excinfo.value.status == 503
    assert "offline" in str(excinfo.value)


def test_a_missing_model_is_a_503_that_says_how_to_fix_it(config, monkeypatch):
    _reply(monkeypatch, 'model "qwen3:4b" not found', status=404)
    with pytest.raises(NlRuleError) as excinfo:
        parse(config, "anything")
    assert excinfo.value.status == 503
    assert "ollama pull" in str(excinfo.value)


def test_a_hallucinated_zone_survives_the_round_trip_as_an_error(config, monkeypatch):
    _reply(monkeypatch, '{"type":"crowd","cam":"cam-01","zone":"the-canteen"}')
    with pytest.raises(NlRuleError) as excinfo:
        parse(config, "alert me about the canteen")
    assert excinfo.value.status == 422
    assert "the-canteen" in str(excinfo.value)
    assert excinfo.value.raw, "the raw output should come back so a person can see it"


def test_an_empty_instruction_is_refused(config):
    with pytest.raises(NlRuleError):
        parse(config, "   ")
