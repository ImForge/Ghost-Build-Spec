"""Tailgating: two *different* people through one door in quick succession."""
from __future__ import annotations

from brain.tests.conftest import T0, Harness, rule_cfg
from tools.synth import Body, bodies_at

DOOR_X = 0.84


def _pair_through(gap_s: float, *, same_person: bool = False):
    """Two crossings `gap_s` apart, optionally reusing one track id."""
    def bodies(elapsed: float) -> list[Body]:
        out: list[Body] = []
        for i, (tid, start) in enumerate(((600, 0.5), (600 if same_person else 601,
                                                       0.5 + gap_s))):
            local = elapsed - start
            if local < 0 or local > 2.5:
                continue
            frac = min(1.0, local / 1.0)
            out.append(Body(tid=tid, x=DOOR_X + 0.02 * i, y=0.80 - 0.20 * frac))
        return out
    return bodies


def test_two_people_close_together_fires(h: Harness):
    fired = h.run("cam-01", 5.0, _pair_through(0.8), rules=["door-1-tail"])
    assert fired
    candidate = fired[0][1]
    assert candidate.line == "door-1"
    assert sorted(candidate.track_ids) == [600, 601]
    assert candidate.features["people"] == 2
    assert "Tailgating at Lab Door" in candidate.message


def test_one_person_alone_does_not_fire(h: Harness):
    def single(elapsed: float) -> list[Body]:
        frac = min(1.0, elapsed / 1.0)
        return [Body(tid=602, x=DOOR_X, y=0.80 - 0.20 * frac)]

    assert h.run("cam-01", 4.0, single, rules=["door-1-tail"]) == []


def test_the_same_track_crossing_twice_is_not_tailgating(h: Harness):
    """A tracker that loses and re-acquires one person must not raise this."""
    def back_and_forth(elapsed: float) -> list[Body]:
        # One id, in then out then in again, all inside the window.
        if elapsed < 0.6:
            return [Body(tid=603, x=DOOR_X, y=0.80)]
        if elapsed < 1.2:
            return [Body(tid=603, x=DOOR_X, y=0.60)]
        if elapsed < 1.8:
            return [Body(tid=603, x=DOOR_X, y=0.80)]
        return [Body(tid=603, x=DOOR_X, y=0.60)]

    fired = h.run("cam-01", 3.0, back_and_forth, rules=["door-1-tail"])
    crossings = h.cam_state("cam-01").recent_crossings("door-1", 5.0, T0 + 3.0)
    assert len(crossings) >= 2, "the setup must actually produce several crossings"
    assert fired == [], "several crossings by one id is not tailgating"


def test_people_further_apart_than_the_window_do_not_fire(h: Harness):
    """window_s is 2.0 s; five seconds apart is two normal entries."""
    assert h.run("cam-01", 9.0, _pair_through(5.0), rules=["door-1-tail"]) == []


def test_the_window_is_configurable(h: Harness):
    cfg = rule_cfg("tailgate", line="door-1", params={"window_s": 8.0})
    fired = h.run_with("cam-01", 9.0, _pair_through(5.0), "tailgate", cfg)
    assert fired, "a wider window must catch the same pair"
    assert fired[0][1].features["window_s"] == 8.0


def test_the_synth_tailgate_scenario_fires_the_shipped_rule(h: Harness):
    fired = h.run("cam-01", 6.0, lambda e: bodies_at("tailgate", e), rules=["door-1-tail"])
    assert fired, "scenario 'tailgate' must trip door-1-tail"
    assert sorted(fired[0][1].track_ids) == [50, 51]
