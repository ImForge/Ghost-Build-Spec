"""Entry into a restricted zone fires on the transition, not on presence."""
from __future__ import annotations

from datetime import datetime

from brain.engine import within_hours
from brain.tests.conftest import T0, Harness
from tools.synth import Body, bodies_at


def _walk_in(elapsed: float) -> list[Body]:
    """From (0.2, 0.9) into server-room (x 0.70-0.98, y 0.20-0.70) on cam-01."""
    return bodies_at("entry", elapsed)


def test_entry_fires_when_someone_walks_into_the_restricted_zone(h: Harness):
    fired = h.run("cam-01", 8.0, _walk_in, rules=["entry-server"])
    assert fired
    candidate = fired[0][1]
    assert candidate.zone == "server-room"
    assert candidate.severity == "high"
    assert candidate.message == "Unauthorized entry into Server Room Door"
    assert candidate.dedupe_key == "entry:cam-01:server-room:40"


def test_entry_fires_on_the_transition_and_then_stops(h: Harness):
    """Standing in a server room for an hour is one alert, not thirty-six thousand."""
    fired = h.run("cam-01", 12.0, _walk_in, rules=["entry-server"])
    assert fired
    window = [t for t, _c in fired]
    assert max(window) - min(window) <= 1.1, (
        "the rule must only consider a track freshly entered for about a second"
    )


def test_walking_past_outside_the_zone_does_not_fire(h: Harness):
    def along_the_bottom(elapsed: float) -> list[Body]:
        return [Body(tid=41, x=0.10 + 0.08 * elapsed, y=0.95)]

    assert h.run("cam-01", 8.0, along_the_bottom, rules=["entry-server"]) == []


def test_someone_already_inside_at_startup_still_fires_once(h: Harness):
    """A track first seen inside the zone has entered it as far as we can tell."""
    fired = h.run("cam-01", 3.0, lambda _e: [Body(tid=42, x=0.84, y=0.45)],
                  rules=["entry-server"])
    assert fired


def test_leaving_and_returning_fires_again(h: Harness):
    def in_out_in(elapsed: float) -> list[Body]:
        inside = elapsed < 2.0 or elapsed > 6.0
        return [Body(tid=43, x=0.84 if inside else 0.20, y=0.45 if inside else 0.95)]

    fired = h.run("cam-01", 10.0, in_out_in, rules=["entry-server"])
    stamps = sorted({round(t - T0, 1) for t, _c in fired})
    assert stamps, "no entry fired at all"
    assert max(stamps) - min(stamps) > 3.0, (
        "a second entry after a genuine exit must fire again"
    )


# --- the hours window the engine applies before the rule runs -----------
def test_hours_window_inside_and_outside():
    assert within_hours([["08:00", "18:00"]], datetime(2026, 1, 1, 12, 0)) is True
    assert within_hours([["08:00", "18:00"]], datetime(2026, 1, 1, 7, 59)) is False
    assert within_hours([["08:00", "18:00"]], datetime(2026, 1, 1, 18, 1)) is False


def test_hours_window_wraps_past_midnight():
    """"After 8pm" is the whole point of a restricted-hours rule."""
    overnight = [["20:00", "06:00"]]
    assert within_hours(overnight, datetime(2026, 1, 1, 23, 30)) is True
    assert within_hours(overnight, datetime(2026, 1, 1, 2, 0)) is True
    assert within_hours(overnight, datetime(2026, 1, 1, 12, 0)) is False


def test_no_hours_means_always_active():
    assert within_hours(None, datetime(2026, 1, 1, 3, 0)) is True
    assert within_hours([], datetime(2026, 1, 1, 3, 0)) is True


def test_malformed_hours_are_ignored_not_fatal():
    assert within_hours([["nonsense"]], datetime(2026, 1, 1, 12, 0)) is False
    assert within_hours([["08:00", "18:00"], ["bad"]],
                        datetime(2026, 1, 1, 12, 0)) is True


def test_the_seeded_entry_rule_is_always_on_so_the_demo_fires(h: Harness):
    cfg = h.config.rule("entry-server")
    assert cfg["hours"] == [["00:00", "23:59"]]
    assert within_hours(cfg["hours"], datetime(2026, 1, 1, 14, 30)) is True
