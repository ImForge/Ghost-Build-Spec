"""Running and fighting: both need the behaviour sustained, not just glimpsed."""
from __future__ import annotations

import pytest

from brain.tests.conftest import Harness, rule_cfg
from tools.synth import Body, bodies_at


def _pace(elapsed: float) -> list[Body]:
    """The synth runner: across the frame in 1.5 s, about 1.8 body-heights/s."""
    return bodies_at("running", elapsed)


def _stroll(elapsed: float) -> list[Body]:
    return [Body(tid=700, x=0.20 + 0.03 * elapsed, y=0.80)]


# --- running ------------------------------------------------------------
def test_running_fires_on_the_run_scenario(h: Harness):
    fired = h.run("cam-01", 4.0, _pace, rules=["running-all"])
    assert fired
    candidate = fired[0][1]
    assert candidate.type == "running"
    assert candidate.track_ids == [60]
    assert candidate.features["speed_bh_s"] >= 1.5
    assert "body-heights/s" in candidate.message


def test_walking_does_not_fire_running(h: Harness):
    assert h.run("cam-01", 6.0, _stroll, rules=["running-all"]) == []


def test_standing_still_does_not_fire_running(h: Harness):
    assert h.run("cam-01", 6.0, lambda _e: [Body(tid=701, x=0.5, y=0.8)],
                 rules=["running-all"]) == []


def test_a_single_fast_frame_is_not_running(h: Harness):
    """Tracker noise is one bad frame; a person is a second of them."""
    def one_jump(elapsed: float) -> list[Body]:
        x = 0.90 if 2.0 <= elapsed < 2.1 else 0.20
        return [Body(tid=702, x=x, y=0.80)]

    assert h.run("cam-01", 5.0, one_jump, rules=["running-all"]) == []


@pytest.mark.parametrize("threshold,should_fire", [(1.0, True), (1.5, True), (5.0, False)])
def test_running_speed_threshold(h: Harness, threshold, should_fire):
    cfg = rule_cfg("running", params={"speed_bh_s": threshold, "sustain_s": 1.0})
    assert bool(h.run_with("cam-01", 4.0, _pace, "running", cfg)) is should_fire


def test_running_needs_the_track_to_be_old_enough(h: Harness):
    """sustain_s of history must exist before a verdict is possible."""
    cfg = rule_cfg("running", params={"speed_bh_s": 1.0, "sustain_s": 3.0})
    assert h.run_with("cam-01", 1.5, _pace, "running", cfg) == []


def test_speed_is_scale_free(h: Harness):
    """A small distant figure moving proportionally must read the same speed."""
    def far_runner(elapsed: float) -> list[Body]:
        phase = (elapsed % 3.0) / 1.5
        frac = phase if phase <= 1.0 else 2.0 - phase
        # Quarter-size body covering a quarter of the distance.
        return [Body(tid=703, x=0.05 + 0.225 * frac, y=0.50, scale=0.25)]

    fired = h.run("cam-01", 4.0, far_runner, rules=["running-all"])
    assert fired, "the same motion in body-heights must fire at any apparent size"


# --- fighting -----------------------------------------------------------
def test_fight_fires_on_the_fight_scenario(h: Harness):
    fired = h.run("cam-01", 5.0, lambda e: bodies_at("fight", e), rules=["fight-all"])
    assert fired
    candidate = fired[0][1]
    assert candidate.type == "fight"
    assert sorted(candidate.track_ids) == [70, 71]
    assert candidate.severity == "critical"
    assert candidate.features["wrist_a_bh_s"] >= 2.0
    assert candidate.features["wrist_b_bh_s"] >= 2.0


def test_two_people_standing_calmly_together_is_a_conversation(h: Harness):
    def chatting(_elapsed: float) -> list[Body]:
        return [Body(tid=710, x=0.48, y=0.82), Body(tid=711, x=0.52, y=0.82)]

    assert h.run("cam-01", 5.0, chatting, rules=["fight-all"]) == []


def test_flailing_alone_is_not_a_fight(h: Harness):
    """Waving at a camera must not summon security."""
    def alone(_elapsed: float) -> list[Body]:
        return [Body(tid=712, x=0.50, y=0.82, wrist_jitter=0.10)]

    assert h.run("cam-01", 5.0, alone, rules=["fight-all"]) == []


def test_flailing_far_apart_is_not_a_fight(h: Harness):
    def apart(_elapsed: float) -> list[Body]:
        return [Body(tid=713, x=0.12, y=0.82, wrist_jitter=0.10),
                Body(tid=714, x=0.88, y=0.82, wrist_jitter=0.10)]

    assert h.run("cam-01", 5.0, apart, rules=["fight-all"]) == []


def test_only_one_of_the_two_flailing_is_not_a_fight(h: Harness):
    """One agitated person next to a calm one is not two people fighting."""
    def one_sided(_elapsed: float) -> list[Body]:
        return [Body(tid=715, x=0.48, y=0.82, wrist_jitter=0.10),
                Body(tid=716, x=0.52, y=0.82, wrist_jitter=0.0)]

    assert h.run("cam-01", 5.0, one_sided, rules=["fight-all"]) == []


def test_fight_needs_proximity_sustained(h: Harness):
    """Brushing past each other while gesturing is not a fight."""
    def passing(elapsed: float) -> list[Body]:
        close = 1.9 <= elapsed <= 2.2
        return [
            Body(tid=717, x=0.48 if close else 0.10, y=0.82, wrist_jitter=0.10),
            Body(tid=718, x=0.52 if close else 0.90, y=0.82, wrist_jitter=0.10),
        ]

    assert h.run("cam-01", 5.0, passing, rules=["fight-all"]) == []


@pytest.mark.parametrize("threshold,should_fire", [(1.0, True), (2.0, True), (20.0, False)])
def test_fight_wrist_threshold(h: Harness, threshold, should_fire):
    cfg = rule_cfg("fight", params={"wrist_speed_bh_s": threshold, "sustain_s": 1.0,
                                    "max_center_dist_bh": 0.6})
    fired = h.run_with("cam-01", 5.0, lambda e: bodies_at("fight", e), "fight", cfg)
    assert bool(fired) is should_fire


def test_pair_state_is_dropped_when_people_separate(h: Harness):
    """Otherwise an old pairing would count toward a later encounter's sustain."""
    def together_then_apart(elapsed: float) -> list[Body]:
        if elapsed < 2.0:
            return [Body(tid=720, x=0.48, y=0.82), Body(tid=721, x=0.52, y=0.82)]
        return [Body(tid=720, x=0.10, y=0.82), Body(tid=721, x=0.90, y=0.82)]

    h.run("cam-01", 4.0, together_then_apart, rules=["fight-all"])
    assert h.cam_state("cam-01").pair_since == {}
