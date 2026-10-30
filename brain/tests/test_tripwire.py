"""Line crossings: the segment test, and direction filtering."""
from __future__ import annotations

import pytest

from brain.tests.conftest import T0, Harness
from tools.synth import Body

# door-1 in config/zones.yaml: a = (0.70, 0.70), b = (0.98, 0.70).
DOOR_X = 0.84


def _through_the_door(elapsed: float, *, reverse: bool = False) -> list[Body]:
    """Feet from y 0.80 to y 0.60 (or back), crossing y 0.70 inside the segment.

    For this line `side` is positive where y > 0.70, so the default direction
    (0.80 -> 0.60) leaves the positive side: a `neg` crossing. `reverse` is the
    `pos` one.
    """
    frac = min(1.0, elapsed / 2.0)
    y = 0.80 - 0.20 * frac if not reverse else 0.60 + 0.20 * frac
    return [Body(tid=500, x=DOOR_X, y=y)]


def _past_the_end(elapsed: float) -> list[Body]:
    """Same vertical move, but at x 0.10 — nowhere near the doorway."""
    frac = min(1.0, elapsed / 2.0)
    return [Body(tid=501, x=0.10, y=0.80 - 0.20 * frac)]


def test_crossing_the_door_fires(h: Harness):
    fired = h.run("cam-01", 3.0, _through_the_door, rules=["door-1-trip"])
    assert fired
    candidate = fired[0][1]
    assert candidate.line == "door-1"
    assert candidate.track_ids == [500]
    assert candidate.dedupe_key == "tripwire:cam-01:door-1:500"


def test_walking_past_the_end_of_the_line_does_not_fire(h: Harness):
    """The side sign flips here too; only the segment test rejects it."""
    assert h.run("cam-01", 3.0, _past_the_end, rules=["door-1-trip"]) == []


def test_moving_parallel_to_the_line_does_not_fire(h: Harness):
    def along_the_line(elapsed: float) -> list[Body]:
        return [Body(tid=502, x=0.72 + 0.10 * min(1.0, elapsed / 2.0), y=0.80)]

    assert h.run("cam-01", 3.0, along_the_line, rules=["door-1-trip"]) == []


def test_a_crossing_fires_once_not_every_tick(h: Harness):
    """The crossing record is authoritative, so a stationary track cannot re-fire."""
    fired = h.run("cam-01", 6.0, _through_the_door, rules=["door-1-trip"])
    assert len(fired) == 1, f"expected one crossing, got {len(fired)}"


@pytest.mark.parametrize(
    "direction,reverse,should_fire",
    [
        ("any", False, True),
        ("any", True, True),
        ("pos", False, False),   # 0.80 -> 0.60 leaves the positive side
        ("pos", True, True),     # 0.60 -> 0.80 enters it
        ("neg", False, True),
        ("neg", True, False),
    ],
)
def test_direction_filtering(h: Harness, direction, reverse, should_fire):
    """`pos` is the crossing whose side sign goes negative to positive."""
    h.config.line("door-1")["direction"] = direction
    fired = h.run("cam-01", 3.0, lambda e: _through_the_door(e, reverse=reverse),
                  rules=["door-1-trip"])
    assert bool(fired) is should_fire, (
        f"direction={direction} reverse={reverse}: expected fire={should_fire}"
    )


def test_the_direction_sign_is_recorded_both_ways(h: Harness):
    """Whatever the rule filters on, state records which way each crossing went."""
    h.run("cam-01", 3.0, _through_the_door, rules=[])
    leaving = h.cam_state("cam-01").recent_crossings("door-1", 10.0, T0 + 3.0)
    assert [c[2] for c in leaving] == [-1], "0.80 -> 0.60 leaves the positive side"

    other = Harness(config=h.config)
    other.run("cam-01", 3.0, lambda e: _through_the_door(e, reverse=True), rules=[])
    entering = other.cam_state("cam-01").recent_crossings("door-1", 10.0, T0 + 3.0)
    assert [c[2] for c in entering] == [1], "0.60 -> 0.80 enters it"


def test_every_crossing_is_recorded_regardless_of_the_rules_direction(h: Harness):
    """Tailgating counts people through the door whichever way the rule filters."""
    h.config.line("door-1")["direction"] = "pos"
    h.run("cam-01", 3.0, _through_the_door, rules=["door-1-trip"])
    recorded = h.cam_state("cam-01").recent_crossings("door-1", 10.0, T0 + 3.0)
    assert len(recorded) == 1, "the crossing still happened even if the rule ignored it"
