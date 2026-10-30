"""Loitering, and the detection-gap grace period that makes it trustworthy."""
from __future__ import annotations

from brain.tests.conftest import Harness, rule_cfg
from tools.synth import Body, bodies_at


def _loiterer(_elapsed: float) -> list[Body]:
    return [Body(tid=800, x=0.45, y=0.82)]


def test_loiter_fires_after_the_dwell_time(h: Harness):
    """loiter-corr's demo dwell is 20 s (config/rules.yaml)."""
    fired = h.run("cam-01", 24.0, _loiterer, rules=["loiter-corr"])
    assert fired
    candidate = fired[0][1]
    assert candidate.zone == "corridor-b"
    assert candidate.track_ids == [800]
    assert candidate.features["dwell_s"] >= 20
    assert "Loitering in Corridor B" in candidate.message


def test_loiter_does_not_fire_early(h: Harness):
    assert h.run("cam-01", 15.0, _loiterer, rules=["loiter-corr"]) == []


def test_passing_through_does_not_loiter(h: Harness):
    def crossing(elapsed: float) -> list[Body]:
        return [Body(tid=801, x=0.10 + 0.10 * elapsed, y=0.82)]

    assert h.run("cam-01", 12.0, crossing, rules=["loiter-corr"]) == []


def test_a_brief_detection_gap_does_not_reset_the_clock(h: Harness):
    """Trackers lose people behind pillars; the dwell timer must survive that."""
    def flickering(elapsed: float) -> list[Body]:
        if 8.0 <= elapsed < 8.9:   # under the 2 s grace period
            return []
        return [Body(tid=802, x=0.45, y=0.82)]

    cfg = rule_cfg("loiter", zone="corridor-b", params={"dwell_s": 12.0})
    fired = h.run_with("cam-01", 16.0, flickering, "loiter", cfg)
    assert fired, "a sub-grace-period gap must not restart the dwell clock"


def test_genuinely_leaving_resets_the_clock(h: Harness):
    """Walking out of the zone for five seconds is leaving, not flickering."""
    def out_and_back(elapsed: float) -> list[Body]:
        if 6.0 <= elapsed < 11.0:
            return [Body(tid=803, x=0.45, y=0.20)]  # above corridor-b
        return [Body(tid=803, x=0.45, y=0.82)]

    cfg = rule_cfg("loiter", zone="corridor-b", params={"dwell_s": 12.0})
    assert h.run_with("cam-01", 16.0, out_and_back, "loiter", cfg) == []


def test_the_synth_loiter_scenario_fires_the_shipped_rule(h: Harness):
    fired = h.run("cam-01", 24.0, lambda e: bodies_at("loiter", e), rules=["loiter-corr"])
    assert fired, "scenario 'loiter' must trip loiter-corr"


def test_production_dwell_would_not_fire_on_the_demo(h: Harness):
    """The seeded 20 s is a demo value; 300 s is the production one."""
    cfg = rule_cfg("loiter", zone="corridor-b", params={"dwell_s": 300})
    assert h.run_with("cam-01", 24.0, _loiterer, "loiter", cfg) == []
