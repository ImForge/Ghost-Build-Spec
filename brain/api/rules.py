"""Rule CRUD. Natural-language rule parsing arrives with brain/nlrules.py."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from brain.api import get_brain
from brain.config import DEFAULT_PARAMS, slugify
from brain.constants import ALERT_TYPES, SEVERITIES
from brain.nlrules import NlRuleError, parse as parse_nl
from brain.rules import registered_types

log = logging.getLogger("brain.api.rules")
router = APIRouter()


class RuleBody(BaseModel):
    """One rule as it appears in rules.yaml (§4.3)."""

    id: str | None = None
    type: str
    cam: str = "*"
    zone: str | None = None
    line: str | None = None
    enabled: bool = True
    severity: str = "medium"
    cooldown_s: float = Field(30.0, ge=0)
    hours: list[list[str]] | None = None
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def _type(cls, v: str) -> str:
        if v not in ALERT_TYPES:
            raise ValueError(f"type must be one of {ALERT_TYPES}")
        return v

    @field_validator("severity")
    @classmethod
    def _severity(cls, v: str) -> str:
        if v not in SEVERITIES:
            raise ValueError(f"severity must be one of {SEVERITIES}")
        return v

    @field_validator("hours")
    @classmethod
    def _hours(cls, v: list[list[str]] | None) -> list[list[str]] | None:
        for window in v or []:
            if len(window) != 2:
                raise ValueError('each hours window is ["HH:MM", "HH:MM"]')
            for stamp in window:
                hh, _, mm = str(stamp).partition(":")
                if not (hh.isdigit() and mm.isdigit() and 0 <= int(hh) < 24 and 0 <= int(mm) < 60):
                    raise ValueError(f"{stamp!r} is not a HH:MM time")
        return v


class RulePatch(BaseModel):
    cam: str | None = None
    zone: str | None = None
    line: str | None = None
    enabled: bool | None = None
    severity: str | None = None
    cooldown_s: float | None = None
    hours: list[list[str]] | None = None
    params: dict[str, Any] | None = None


def validate_targets(brain: Any, rule: dict[str, Any]) -> None:
    """A rule pointing at a zone or cam that does not exist would silently never fire."""
    cam = rule.get("cam", "*")
    if cam != "*" and brain.config.cam(cam) is None:
        raise HTTPException(422, f"no such cam: {cam}")
    if rule.get("zone") and brain.config.zone(str(rule["zone"])) is None:
        raise HTTPException(422, f"no such zone: {rule['zone']}")
    if rule.get("line") and brain.config.line(str(rule["line"])) is None:
        raise HTTPException(422, f"no such line: {rule['line']}")


@router.get("/rules")
def list_rules(brain=Depends(get_brain)) -> list[dict[str, Any]]:
    return brain.config.rules


@router.get("/rules/types")
def rule_types() -> dict[str, Any]:
    """What the engine can actually evaluate, with each type's default params."""
    return {
        "types": list(ALERT_TYPES),
        "implemented": registered_types(),
        "defaults": DEFAULT_PARAMS,
        "severities": list(SEVERITIES),
    }


class NlRuleRequest(BaseModel):
    text: str


@router.post("/rules/nl")
def parse_natural_language(body: NlRuleRequest, brain=Depends(get_brain)) -> dict[str, Any]:
    """Translate a sentence into a rule — and return it WITHOUT saving.

    The dashboard shows the result as a filled-in form so a person confirms it
    before it becomes a rule. A model that invents a zone id would otherwise
    install something that silently never fires.
    """
    try:
        rule = parse_nl(brain.config, body.text)
    except NlRuleError as exc:
        raise HTTPException(
            exc.status, {"message": str(exc), "raw": exc.raw} if exc.raw else str(exc)
        ) from exc
    brain.db.audit("api", "rule_nl", rule["type"], {"text": body.text[:200]})
    return {"rule": rule, "saved": False}


@router.post("/rules", status_code=201)
def create_rule(body: RuleBody, brain=Depends(get_brain)) -> dict[str, Any]:
    rule = body.model_dump(exclude_none=False)
    if rule.get("hours") is None:
        rule.pop("hours", None)
    rule["id"] = str(rule["id"] or _unique_rule_id(brain, body))
    validate_targets(brain, rule)
    if brain.config.rule(rule["id"]):
        raise HTTPException(409, f"rule {rule['id']} already exists")
    rule["params"] = {**DEFAULT_PARAMS.get(rule["type"], {}), **(rule.get("params") or {})}
    brain.config.rules.append(rule)
    brain.config.save_rules()
    brain.db.audit("api", "config_change", f"rule:{rule['id']}", "created")
    log.info("rule %s created (%s on %s)", rule["id"], rule["type"], rule["cam"])
    return rule


def _unique_rule_id(brain: Any, body: RuleBody) -> str:
    base = slugify(f"{body.type}-{body.zone or body.line or body.cam}")
    taken = {r["id"] for r in brain.config.rules}
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


@router.put("/rules/{rule_id}")
def update_rule(rule_id: str, body: RulePatch, brain=Depends(get_brain)) -> dict[str, Any]:
    rule = brain.config.rule(rule_id)
    if rule is None:
        raise HTTPException(404, f"no such rule: {rule_id}")
    changes = body.model_dump(exclude_none=True)
    if "severity" in changes and changes["severity"] not in SEVERITIES:
        raise HTTPException(422, f"severity must be one of {SEVERITIES}")
    merged = {**rule, **changes}
    validate_targets(brain, merged)
    if "params" in changes:
        changes["params"] = {
            **DEFAULT_PARAMS.get(rule["type"], {}), **(changes["params"] or {})
        }
    rule.update(changes)
    brain.config.save_rules()
    brain.db.audit("api", "config_change", f"rule:{rule_id}", changes)
    return rule


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: str, brain=Depends(get_brain)) -> dict[str, Any]:
    rule = brain.config.rule(rule_id)
    if rule is None:
        raise HTTPException(404, f"no such rule: {rule_id}")
    brain.config.rules.remove(rule)
    brain.config.save_rules()
    brain.db.audit("api", "config_change", f"rule:{rule_id}", "deleted")
    return {"deleted": rule_id}
