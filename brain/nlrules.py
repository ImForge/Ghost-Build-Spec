"""Turning a sentence into a rule.

"Alert if more than four people are near the bench after 6pm" is how a guard
describes what they want. rules.yaml is how the engine needs it. This asks a
local text model to do the translation, then refuses to trust it: the output is
validated against the same schema the REST API enforces, every camera, zone and
line it names is checked for existence, and the result is returned for a person
to confirm rather than saved.

That last part matters. A model that hallucinates a zone id would otherwise
install a rule that silently never fires, which is worse than an error.
"""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

import requests

from brain.config import DEFAULT_PARAMS
from brain.constants import ALERT_TYPES, SEVERITIES
from brain.settings import settings

if TYPE_CHECKING:  # pragma: no cover
    from brain.config import ConfigStore

log = logging.getLogger("brain.nlrules")

TIMEOUT_S = 60.0
FALLBACK_MODEL = "llama3.2:3b"

SYSTEM = """You convert a security instruction into one JSON rule for a campus
safety system. Reply with JSON only — no prose, no code fences.

The JSON object has these fields:
  type      one of: {types}
  cam       a camera id from the list below, or "*" for every camera
  zone      a zone id from the list below, or null
  line      a line id from the list below, or null
  severity  one of: {severities}
  cooldown_s  seconds to wait before raising the same alert again
  hours     a list of ["HH:MM","HH:MM"] windows in 24-hour local time, or null
            for always. A window may wrap past midnight.
  params    thresholds for this type, using only the keys shown below

Rule types and their params:
{params}

Which type to choose:
  crowd     too many people in a zone, or filling too fast
  crush     people packed dangerously close (needs a calibrated camera)
  fall      someone collapses and stays down
  entry     someone enters a restricted zone
  tripwire  someone crosses a line
  tailgate  two people through one door together
  loiter    someone stays in a zone too long
  running   someone moving fast
  fight     two people close together with arms moving fast
  anomaly   unusual activity compared to what this camera learned

Cameras:
{cams}
Zones:
{zones}
Lines:
{lines}

Examples:
{examples}
"""

EXAMPLES = [
    (
        "tell me if more than 10 people gather at the main gate",
        {"type": "crowd", "cam": "cam-02", "zone": "gate-area", "line": None,
         "severity": "high", "cooldown_s": 60, "hours": None,
         "params": {"max_count": 10}},
    ),
    (
        "nobody should be in the server room between 8pm and 6am",
        {"type": "entry", "cam": "cam-01", "zone": "server-room", "line": None,
         "severity": "critical", "cooldown_s": 60,
         "hours": [["20:00", "06:00"]], "params": {}},
    ),
    (
        "warn me if anyone hangs around the corridor for more than five minutes",
        {"type": "loiter", "cam": "cam-01", "zone": "corridor-b", "line": None,
         "severity": "low", "cooldown_s": 300, "hours": None,
         "params": {"dwell_s": 300}},
    ),
]


class NlRuleError(RuntimeError):
    """Carries the model's raw output so the user can see what it actually said."""

    def __init__(self, message: str, raw: str = "", status: int = 422) -> None:
        super().__init__(message)
        self.raw = raw
        self.status = status


def build_prompt(config: ConfigStore, text: str) -> str:
    def listing(items: list[dict[str, Any]], extra: str = "") -> str:
        if not items:
            return "  (none configured)"
        return "\n".join(
            f"  {i['id']} — {i.get('name', i['id'])}"
            f"{' (' + str(i.get(extra)) + ')' if extra and i.get(extra) else ''}"
            for i in items
        )

    params = "\n".join(
        f"  {name}: {', '.join(sorted(p)) if p else '(none)'}"
        for name, p in DEFAULT_PARAMS.items()
    )
    examples = "\n".join(
        f'  "{q}" -> {json.dumps(a, separators=(",", ":"))}' for q, a in EXAMPLES
    )
    system = SYSTEM.format(
        types=", ".join(ALERT_TYPES),
        severities=", ".join(SEVERITIES),
        params=params,
        cams=listing(config.cams),
        zones=listing(config.zones, "kind"),
        lines=listing(config.lines, "kind"),
        examples=examples,
    )
    return f"{system}\nInstruction: {text}\nJSON:"


def extract_json(raw: str) -> dict[str, Any]:
    """Pull an object out of whatever the model returned.

    `format: json` usually makes this unnecessary, but smaller models still wrap
    their answer in prose or a code fence often enough to be worth handling.
    """
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        parsed = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise NlRuleError("the model did not return any JSON", raw) from None
        try:
            parsed = json.loads(text[start:end + 1])
        except ValueError as exc:
            raise NlRuleError(f"the model's JSON did not parse: {exc}", raw) from exc
    if not isinstance(parsed, dict):
        raise NlRuleError("the model returned JSON that is not an object", raw)
    return parsed


def validate(config: ConfigStore, parsed: dict[str, Any], raw: str = "") -> dict[str, Any]:
    """Normalise and check. Everything the model names must actually exist."""
    rule_type = str(parsed.get("type", "")).strip().lower()
    if rule_type not in ALERT_TYPES:
        raise NlRuleError(
            f"'{rule_type or '(missing)'}' is not a rule type; expected one of "
            f"{', '.join(ALERT_TYPES)}",
            raw,
        )

    cam = str(parsed.get("cam") or "*").strip()
    if cam != "*" and config.cam(cam) is None:
        raise NlRuleError(f"there is no camera called '{cam}'", raw)

    zone = parsed.get("zone") or None
    if zone is not None:
        zone = str(zone).strip()
        if config.zone(zone) is None:
            known = ", ".join(z["id"] for z in config.zones) or "none configured"
            raise NlRuleError(f"there is no zone called '{zone}' (known: {known})", raw)

    line = parsed.get("line") or None
    if line is not None:
        line = str(line).strip()
        if config.line(line) is None:
            known = ", ".join(ln["id"] for ln in config.lines) or "none configured"
            raise NlRuleError(f"there is no line called '{line}' (known: {known})", raw)

    severity = str(parsed.get("severity") or "medium").strip().lower()
    if severity not in SEVERITIES:
        severity = "medium"

    try:
        cooldown = float(parsed.get("cooldown_s", 30) or 30)
    except (TypeError, ValueError):
        cooldown = 30.0

    hours = parsed.get("hours") or None
    if hours is not None:
        hours = _clean_hours(hours, raw)

    allowed = DEFAULT_PARAMS.get(rule_type, {})
    params: dict[str, Any] = {}
    for key, value in (parsed.get("params") or {}).items():
        if key not in allowed:
            log.info("dropping unknown param %r for a %s rule", key, rule_type)
            continue
        try:
            params[key] = float(value)
        except (TypeError, ValueError):
            log.info("dropping non-numeric param %r=%r", key, value)

    rule: dict[str, Any] = {
        "type": rule_type,
        "cam": cam,
        "zone": zone,
        "line": line,
        "enabled": True,
        "severity": severity,
        "cooldown_s": cooldown,
        "params": {**allowed, **params},
    }
    if hours:
        rule["hours"] = hours
    return rule


def _clean_hours(hours: Any, raw: str) -> list[list[str]]:
    if not isinstance(hours, list):
        raise NlRuleError("hours must be a list of [start, end] pairs", raw)
    out: list[list[str]] = []
    for window in hours:
        if not isinstance(window, (list, tuple)) or len(window) != 2:
            raise NlRuleError(f"{window!r} is not a [start, end] pair", raw)
        pair = []
        for stamp in window:
            match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(stamp))
            if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
                raise NlRuleError(f"'{stamp}' is not a HH:MM time", raw)
            pair.append(f"{int(match.group(1)):02d}:{match.group(2)}")
        out.append(pair)
    return out


def parse(config: ConfigStore, text: str, model: str | None = None) -> dict[str, Any]:
    """Ask the model, then refuse to trust it. Returns a rule, unsaved."""
    if not text.strip():
        raise NlRuleError("say what the rule should do")

    payload = {
        "model": model or settings.nlrule_model,
        "prompt": build_prompt(config, text),
        "format": "json",
        "stream": False,
    }
    try:
        resp = requests.post(
            f"{settings.ollama_url}/api/generate", json=payload, timeout=TIMEOUT_S
        )
    except requests.RequestException as exc:
        raise NlRuleError(
            f"narrator/LLM offline — cannot reach ollama at {settings.ollama_url}",
            status=503,
        ) from exc

    if resp.status_code != 200:
        raise NlRuleError(
            f"narrator/LLM offline — ollama returned {resp.status_code}. Is "
            f"'{payload['model']}' pulled? Try: ollama pull {payload['model']}",
            resp.text[:400], status=503,
        )

    try:
        raw = str(resp.json().get("response", ""))
    except ValueError as exc:
        raise NlRuleError("ollama returned a body that is not JSON", status=503) from exc

    rule = validate(config, extract_json(raw), raw)
    log.info("parsed %r into a %s rule on %s", text[:60], rule["type"], rule["cam"])
    return rule
