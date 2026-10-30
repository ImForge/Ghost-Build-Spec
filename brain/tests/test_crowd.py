"""Crowding by count, by density and by rate of change — plus crush risk."""
from __future__ import annotations

from brain.geometry import homography_from_points
from brain.tests.conftest import T0, Harness, rule_cfg
from tools.synth import Body, bodies_at


def _cluster(n: int, *, tight: bool = False) -> list[Body]:
    """n people inside corridor-b / gate-area (both cover y 0.30-0.98)."""
    spread = 0.03 if tight else 0.35
    return [
        Body(tid=200 + i, x=0.5 + spread * ((i % 5) - 2) / 2.0,
             y=0.70 + spread * ((i // 5) - 1) / 3.0)
        for i in range(n)
    ]


def test_crowd_fires_on_count(h: Harness):
    """corridor-b's limit is 6 people (config/rules.yaml)."""
    fired = h.run("cam-01", 2.0, lambda _e: _cluster(8), rules=["crowd-corr"])
    assert fired
    candidate = fired[0][1]
    assert candidate.zone == "corridor-b"
    assert candidate.features["count"] == 8
    assert "8 people" in candidate.message


def test_a_small_group_does_not_fire(h: Harness):
    assert h.run("cam-01", 3.0, lambda _e: _cluster(3), rules=["crowd-corr"]) == []


def test_crowd_fires_on_rate_of_change_alone(h: Harness):
    """Five people arriving in ten seconds matters even under the count limit.

    max_count is raised out of the way so only the rate test can trip.
    """
    cfg = rule_cfg("crowd", zone="corridor-b",
                   params={"max_count": 999, "max_density": 999, "rate_per_s": 0.4})

    def ramp(elapsed: float) -> list[Body]:
        return _cluster(1 + int(elapsed / 1.5))

    fired = h.run_with("cam-01", 14.0, ramp, "crowd", cfg)
    assert fired, "a steady influx must fire even with the count below the limit"
    candidate = fired[0][1]
    assert candidate.features["rate_per_s"] >= 0.4
    assert "filling at" in candidate.message


def test_a_steady_population_does_not_trip_the_rate_test(h: Harness):
    cfg = rule_cfg("crowd", zone="corridor-b",
                   params={"max_count": 999, "max_density": 999, "rate_per_s": 0.4})
    assert h.run_with("cam-01", 14.0, lambda _e: _cluster(5), "crowd", cfg) == []


def test_crowd_fires_on_density_alone(h: Harness):
    """bench-1 is 2 m²; four people standing on it is 2 per m²."""
    cfg = rule_cfg("crowd", zone="bench-1",
                   params={"max_count": 999, "max_density": 1.5, "rate_per_s": 999})

    def on_the_bench(_elapsed: float) -> list[Body]:
        return [Body(tid=300 + i, x=0.10 + 0.04 * i, y=0.70) for i in range(4)]

    fired = h.run_with("cam-01", 2.0, on_the_bench, "crowd", cfg)
    assert fired
    assert fired[0][1].features["density"] >= 1.5
    assert "people/m²" in fired[0][1].message


def test_people_outside_the_zone_are_not_counted(h: Harness):
    """corridor-b starts at y 0.35; a crowd above that line is a different place."""
    def above_the_zone(_elapsed: float) -> list[Body]:
        return [Body(tid=400 + i, x=0.2 + 0.07 * i, y=0.30) for i in range(10)]

    assert h.run("cam-01", 2.0, above_the_zone, rules=["crowd-corr"]) == []


def test_the_synth_crowd_scenario_fires_the_shipped_rule(h: Harness):
    """The demo must work with the configuration as committed."""
    fired = h.run("cam-02", 24.0, lambda e: bodies_at("crowd", e), rules=["crowd-gate"])
    assert fired, "scenario 'crowd' must trip crowd-gate on cam-02"
    reasons = " ".join(c.message for _t, c in fired)
    assert "filling at" in reasons, "the gate's count limit is 15, so rate is the trigger"


# --- crush --------------------------------------------------------------
IDENTITY_CAL = homography_from_points(
    [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
    [[100, 100], [700, 100], [700, 500], [100, 500]],
)


def test_crush_needs_a_calibrated_camera(h: Harness):
    """Without a homography there is no metre scale, so no honest crush claim."""
    assert h.config.cam("cam-01")["homography"] is None
    assert h.run("cam-01", 2.0, lambda _e: _cluster(6, tight=True), rules=["crush-all"]) == []


def test_crush_fires_on_a_tight_cluster_once_calibrated(h: Harness):
    h.config.cam("cam-01")["homography"] = IDENTITY_CAL
    fired = h.run("cam-01", 2.0, lambda _e: _cluster(6, tight=True), rules=["crush-all"])
    assert fired
    candidate = fired[0][1]
    assert candidate.severity == "critical"
    assert candidate.features["nn_dist_m"] <= 0.5
    assert candidate.features["people"] >= 4


def test_a_spread_out_group_is_not_a_crush(h: Harness):
    h.config.cam("cam-01")["homography"] = IDENTITY_CAL
    assert h.run("cam-01", 2.0, lambda _e: _cluster(6), rules=["crush-all"]) == []


def test_crush_needs_min_people(h: Harness):
    h.config.cam("cam-01")["homography"] = IDENTITY_CAL
    assert h.run("cam-01", 2.0, lambda _e: _cluster(3, tight=True), rules=["crush-all"]) == []
