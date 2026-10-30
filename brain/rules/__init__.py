"""Rule registry.

Each rule module registers its class here, so adding a detector is one file plus
one import. `brain/engine.py` only ever looks rules up by their `type` string,
which is also what appears in rules.yaml and in an alert's `type` field.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from brain.rules.base import Rule

log = logging.getLogger("brain.rules")

_REGISTRY: dict[str, Rule] = {}


def register(rule: Rule) -> Rule:
    if rule.type in _REGISTRY:
        log.warning("rule type %r registered twice", rule.type)
    _REGISTRY[rule.type] = rule
    return rule


def get_rule(rule_type: str) -> Rule | None:
    return _REGISTRY.get(rule_type)


def registered_types() -> list[str]:
    return sorted(_REGISTRY)


RULE_MODULES = ("fall", "crowd", "entry", "tripwire", "loiter", "motion", "anomaly_rule")


def load_all() -> None:
    """Import every rule module so the registry is populated."""
    import importlib

    for name in RULE_MODULES:
        try:
            importlib.import_module(f"brain.rules.{name}")
        except ModuleNotFoundError as exc:
            # Named explicitly so a genuinely broken import still surfaces
            # rather than being mistaken for a module that is simply absent.
            if exc.name == f"brain.rules.{name}":
                log.warning("rule module %s is not present yet", name)
            else:
                raise
