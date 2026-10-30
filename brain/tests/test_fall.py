"""Falls — and, more importantly, the things that must not be read as falls."""
from __future__ import annotations

import pytest

from brain.tests.conftest import T0, Harness, rule_cfg
from tools.synth import Body, bodies_at


def _play_fall(h: Harness, cam: str = "cam-01"):
    """The synth fall: walk 5 s, drop over 0.5 s, lie still. Returns what fired."""
    return h.run(cam, 9.0, lambda e: bodies_at("fall", e), rules=["fall-all"])


def _sit_down(elapsed: float) -> list[Body]:
    """A slow sit: shallow drop, still upright, box still taller than wide.

    This is the case the rule exists to get right — a bench at lunchtime must
    not produce a critical alert.
    """
    tid = 90
    if elapsed < 3.0:
        return [Body(tid=tid, x=0.50, y=0.80)]
    frac = min(1.0, (elapsed - 3.0) / 1.5)
    # Drops gently and keeps a standing aspect ratio throughout.
    return [Body(tid=tid, x=0.50, y=0.80 + 0.06 * frac, scale=1.0 - 0.25 * frac)]


def test_fall_fires_on_the_fall_sequence(h: Harness):
    fired = _play_fall(h)
    assert fired, "the canonical fall sequence must raise a fall candidate"
    _t, candidate = fired[0]
    assert candidate.type == "fall"
    assert candidate.severity == "critical"
    assert candidate.track_ids == [30]
    assert candidate.dedupe_key == "fall:cam-01:30"


def test_fall_fires_only_after_stillness_has_accumulated(h: Harness):
    """The drop is at t+5.0; the alert must wait for still_s to pass, not race it."""
    fired = _play_fall(h)
    first_at = fired[0][0] - T0
    assert first_at >= 7.0, f"fired at {first_at:.1f}s, before stillness could be established"


def test_fall_message_and_features_are_populated(h: Harness):
    candidate = _play_fall(h)[0][1]
    assert "dropped" in candidate.message and "still for" in candidate.message
    features = candidate.features
    assert features["drop_rate"] >= 1.0
    assert features["torso_angle"] >= 60
    assert features["aspect"] >= 1.2
    assert features["still_s"] >= 1.5


def test_sitting_down_does_not_fire(h: Harness):
    """Shallow drop, upright torso, tall bounding box: not a fall."""
    assert h.run("cam-01", 8.0, _sit_down, rules=["fall-all"]) == []


def test_walking_does_not_fire(h: Harness):
    assert h.run("cam-01", 10.0, lambda e: bodies_at("walk", e), rules=["fall-all"]) == []


def test_running_does_not_fire_a_fall(h: Harness):
    assert h.run("cam-01", 8.0, lambda e: bodies_at("running", e), rules=["fall-all"]) == []


def test_a_fall_needs_stillness_afterwards(h: Harness):
    """Someone who drops and immediately gets up has not fallen, they tripped."""
    def dropped_then_moving(elapsed: float):
        if elapsed < 2.0:
            return [Body(tid=31, x=0.50, y=0.80)]
        if elapsed < 2.5:
            return [Body(tid=31, x=0.50, y=0.80 + 0.30 * ((elapsed - 2.0) / 0.5),
                         lying=elapsed > 2.25)]
        # Lying but sliding across the floor: fails still_max_disp.
        return [Body(tid=31, x=0.50 + 0.10 * (elapsed - 2.5), y=0.95, lying=True)]

    assert h.run("cam-01", 8.0, dropped_then_moving, rules=["fall-all"]) == []


def test_fall_needs_a_minimum_of_samples(h: Harness):
    """Three frames is not enough evidence to call a critical alert."""
    for i in range(3):
        h.feed("cam-01", T0 + i * 0.1, [Body(tid=32, x=0.5, y=0.95, lying=True)])
    assert h.evaluate("fall-all", T0 + 0.3) == []


def test_fall_expires_after_four_seconds(h: Harness):
    """The alert is news when it happens; six seconds later it is history."""
    fired = _play_fall(h)
    assert fired
    last_t = fired[-1][0]
    assert h.evaluate("fall-all", last_t + 6.0) == [], "a stale drop must stop firing"


def test_fall_on_furniture_is_suppressed(h: Harness):
    """bench-1 covers x 0.05-0.30, y 0.60-0.80 on cam-01 (config/zones.yaml)."""
    def fall_on_bench(elapsed: float):
        tid = 33
        if elapsed < 3.0:
            return [Body(tid=tid, x=0.17, y=0.65)]
        if elapsed < 3.5:
            frac = (elapsed - 3.0) / 0.5
            return [Body(tid=tid, x=0.17, y=0.65 + 0.10 * frac, lying=frac > 0.5)]
        return [Body(tid=tid, x=0.17, y=0.75, lying=True)]

    assert h.run("cam-01", 9.0, fall_on_bench, rules=["fall-all"]) == [], \
        "a fall whose feet are on a bench is a sit"


def test_the_same_fall_off_the_bench_does_fire(h: Harness):
    """Control for the furniture test: the suppression must be about the zone."""
    def fall_beside_bench(elapsed: float):
        tid = 34
        if elapsed < 3.0:
            return [Body(tid=tid, x=0.60, y=0.65)]
        if elapsed < 3.5:
            frac = (elapsed - 3.0) / 0.5
            return [Body(tid=tid, x=0.60, y=0.65 + 0.10 * frac, lying=frac > 0.5)]
        return [Body(tid=tid, x=0.60, y=0.75, lying=True)]

    assert h.run("cam-01", 9.0, fall_beside_bench, rules=["fall-all"]), \
        "the same movement away from furniture must still fire"


@pytest.mark.parametrize("drop_rate_threshold,should_fire", [(0.5, True), (99.0, False)])
def test_drop_rate_threshold_is_respected(h: Harness, drop_rate_threshold, should_fire):
    cfg = rule_cfg("fall", params={"drop_rate": drop_rate_threshold})
    fired = h.run_with("cam-01", 9.0, lambda e: bodies_at("fall", e), "fall", cfg)
    assert bool(fired) is should_fire


@pytest.mark.parametrize("aspect_min,should_fire", [(1.2, True), (5.0, False)])
def test_aspect_threshold_is_respected(h: Harness, aspect_min, should_fire):
    cfg = rule_cfg("fall", params={"aspect_min": aspect_min})
    fired = h.run_with("cam-01", 9.0, lambda e: bodies_at("fall", e), "fall", cfg)
    assert bool(fired) is should_fire


@pytest.mark.parametrize("angle,should_fire", [(60, True), (89.9, True), (120, False)])
def test_torso_angle_threshold_is_respected(h: Harness, angle, should_fire):
    cfg = rule_cfg("fall", params={"torso_angle_deg": angle})
    fired = h.run_with("cam-01", 9.0, lambda e: bodies_at("fall", e), "fall", cfg)
    assert bool(fired) is should_fire


def test_a_fallen_track_is_kept_alive_longer_than_a_normal_one(h: Harness):
    """A person who fell stops being detected; forgetting them mid-alert is wrong."""
    fired = _play_fall(h)
    t = fired[-1][0]
    state = h.cam_state("cam-01")
    assert state.tracks[30].drop_at is not None
    state.prune(t + 2.5)   # past the normal 2.0 s TTL
    assert 30 in state.tracks, "a fall candidate must survive the normal track TTL"
    state.prune(t + 3.5)   # past the extended TTL
    assert 30 not in state.tracks
