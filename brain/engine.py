"""The 10 Hz rule tick.

One thread evaluates every enabled rule against every camera it applies to and
hands the resulting candidates to the alert pipeline. Rules themselves are pure:
they read state and return candidates, they never publish or write to the
database. That is what makes each of them testable from a hand-built history.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable

from brain.constants import ENGINE_HZ
from brain.rules import get_rule

if TYPE_CHECKING:  # pragma: no cover
    from brain.config import ConfigStore
    from brain.rules.base import Candidate
    from brain.state import World

log = logging.getLogger("brain.engine")

CandidateSink = Callable[[Any, dict[str, Any]], None]


def within_hours(windows: list[list[str]] | None, when: datetime) -> bool:
    """Is `when` inside any ["HH:MM", "HH:MM"] window? Midnight wrap included."""
    if not windows:
        return True
    minutes = when.hour * 60 + when.minute
    for window in windows:
        try:
            start_s, end_s = window[0], window[1]
            sh, sm = (int(v) for v in str(start_s).split(":"))
            eh, em = (int(v) for v in str(end_s).split(":"))
        except (IndexError, TypeError, ValueError):
            log.warning("ignoring malformed hours window %r", window)
            continue
        start, end = sh * 60 + sm, eh * 60 + em
        if start <= end:
            if start <= minutes <= end:
                return True
        elif minutes >= start or minutes <= end:  # wraps past midnight
            return True
    return False


class Engine:
    def __init__(self, world: World, config: ConfigStore, sink: CandidateSink,
                 hz: float = ENGINE_HZ) -> None:
        self.world = world
        self.config = config
        self.sink = sink
        self.period = 1.0 / hz
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.ticks = 0
        self.candidates = 0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="engine", daemon=True)
        self._thread.start()
        log.info("engine running at %.0f Hz", 1.0 / self.period)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        log.info("engine stopped after %d ticks, %d candidates", self.ticks, self.candidates)

    def _run(self) -> None:
        while not self._stop.wait(self.period):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 — a bad tick must not kill the engine
                log.exception("rule tick failed")

    def tick(self, now: float | None = None) -> list[Candidate]:
        """Evaluate every applicable rule once. Returns the candidates raised."""
        now = time.time() if now is None else now
        self.ticks += 1
        local = datetime.fromtimestamp(now)
        active = self.world.active_cams()
        produced: list[Candidate] = []

        for rule_cfg in list(self.config.rules):
            if not rule_cfg.get("enabled", True):
                continue
            rule = get_rule(str(rule_cfg.get("type")))
            if rule is None:
                continue
            if not within_hours(rule_cfg.get("hours"), local):
                continue

            for cam_id in self.config.cams_matching(str(rule_cfg.get("cam", "*")), active):
                cam_state = self.world.cams.get(cam_id)
                if cam_state is None or not cam_state.active:
                    continue
                try:
                    with self.world.lock:
                        candidates = rule.evaluate(cam_state, rule_cfg, now, self.config)
                except Exception:  # noqa: BLE001 — isolate one bad rule
                    log.exception("rule %s failed on %s", rule_cfg.get("id"), cam_id)
                    continue
                for candidate in candidates:
                    produced.append(candidate)
                    self.candidates += 1
                    self.sink(candidate, rule_cfg)
        return produced
