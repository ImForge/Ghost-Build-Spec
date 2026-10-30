"""Test harness for the rule engine.

Rules are exercised through the real ingest path — events in, state updated,
rule evaluated — rather than against hand-built TrackState objects. That way a
test failure means the system is wrong, not just that one function is.

Bodies come from tools/synth.py, so the tests and the demo share one definition
of what a standing or fallen person looks like. If a scenario stops firing, these
tests say so before the rehearsal does.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from brain.config import ConfigStore  # noqa: E402
from brain.rules import get_rule, load_all  # noqa: E402
from brain.rules.base import Candidate  # noqa: E402
from brain.state import CamState, World  # noqa: E402
from tools.synth import Body, person_json  # noqa: E402

load_all()

T0 = 1_700_000_000.0  # a fixed epoch so timestamps in failures are readable


@dataclass
class Harness:
    """Feeds synthetic events into a real World and evaluates real rules."""

    config: ConfigStore
    world: World = field(default_factory=World)
    rng: np.random.Generator = field(default_factory=lambda: np.random.default_rng(0))
    seq: int = 0

    def feed(self, cam: str, t: float, bodies: Sequence[Body]) -> CamState:
        self.seq += 1
        event = {
            "cam": cam, "ts": t, "seq": self.seq, "w": 640, "h": 480,
            "people": [person_json(b, self.rng) for b in bodies],
            "count": len(bodies),
        }
        return self.world.ingest(event, self.config.zones, self.config.lines, now=t)

    def feed_series(self, cam: str, start: float, duration: float,
                    bodies_at: Any, fps: float = 10.0) -> float:
        """Play `bodies_at(elapsed)` for `duration` seconds. Returns the last t."""
        steps = max(1, int(duration * fps))
        t = start
        for i in range(steps):
            t = start + i / fps
            self.feed(cam, t, bodies_at(i / fps))
        return t

    def hold(self, cam: str, start: float, duration: float,
             bodies: Sequence[Body], fps: float = 10.0) -> float:
        return self.feed_series(cam, start, duration, lambda _e: bodies, fps)

    def run(self, cam: str, duration: float, bodies_at: Any, rules: Sequence[str] = (),
            start: float = T0, fps: float = 10.0) -> list[tuple[float, Candidate]]:
        """Feed events AND evaluate rules every frame, the way the engine does.

        Several rules are stateful across ticks on purpose — the fall rule marks
        `drop_at` on a track while the drop is still inside its look-back window
        and then waits for stillness to accumulate. Evaluating only at the end of
        a sequence would miss exactly the behaviour being tested.
        """
        fired: list[tuple[float, Candidate]] = []
        steps = max(1, int(duration * fps))
        for i in range(steps):
            t = start + i / fps
            self.feed(cam, t, bodies_at(i / fps))
            for rule_id in rules:
                for candidate in self.evaluate(rule_id, t, cam=cam):
                    fired.append((t, candidate))
        return fired

    def run_with(self, cam: str, duration: float, bodies_at: Any, rule_type: str,
                 cfg: dict[str, Any], start: float = T0,
                 fps: float = 10.0) -> list[tuple[float, Candidate]]:
        """Same, for an ad-hoc rule config used to probe one threshold."""
        fired: list[tuple[float, Candidate]] = []
        steps = max(1, int(duration * fps))
        for i in range(steps):
            t = start + i / fps
            self.feed(cam, t, bodies_at(i / fps))
            for candidate in self.evaluate_with(rule_type, cfg, t, cam=cam):
                fired.append((t, candidate))
        return fired

    def evaluate(self, rule_id: str, t: float, cam: str | None = None) -> list[Candidate]:
        cfg = self.config.rule(rule_id)
        if cfg is None:
            raise AssertionError(f"no rule {rule_id!r} in config/rules.yaml")
        rule = get_rule(str(cfg["type"]))
        if rule is None:
            raise AssertionError(f"rule type {cfg['type']!r} is not registered")
        cam_id = cam or (str(cfg["cam"]) if cfg["cam"] != "*" else "cam-01")
        return rule.evaluate(self.world.cam(cam_id), cfg, t, self.config)

    def evaluate_with(self, rule_type: str, rule_cfg: dict[str, Any], t: float,
                      cam: str = "cam-01") -> list[Candidate]:
        """Evaluate an ad-hoc rule config, for testing thresholds directly."""
        rule = get_rule(rule_type)
        assert rule is not None, f"rule type {rule_type!r} is not registered"
        return rule.evaluate(self.world.cam(cam), rule_cfg, t, self.config)

    def cam_state(self, cam: str = "cam-01") -> CamState:
        return self.world.cam(cam)


@pytest.fixture
def config() -> ConfigStore:
    """The real config/ files, so tests fail if the shipped demo would not fire."""
    return ConfigStore()


@pytest.fixture
def h(config: ConfigStore) -> Harness:
    return Harness(config=config)


def rule_cfg(rule_type: str, **overrides: Any) -> dict[str, Any]:
    """A minimal rule config with the type's defaults, for threshold tests."""
    from brain.config import DEFAULT_PARAMS

    cfg: dict[str, Any] = {
        "id": f"test-{rule_type}", "type": rule_type, "cam": "*", "enabled": True,
        "severity": "medium", "cooldown_s": 0.0,
        "params": {**DEFAULT_PARAMS.get(rule_type, {}), **(overrides.pop("params", {}) or {})},
    }
    cfg.update(overrides)
    return cfg


def types_of(candidates: Iterable[Candidate]) -> list[str]:
    return [c.type for c in candidates]
