"""Config files: load, hot-reload, atomic write.

cams.yaml, zones.yaml and rules.yaml are the brain's only mutable configuration.
They are re-read whenever their mtime changes (checked every 2 s), and every
write from the REST API goes to a temp file and is renamed over the original, so
a half-written file is never visible to the reload.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import unicodedata
from pathlib import Path
from typing import Any

import yaml

from brain.constants import SEVERITIES, ZONE_KINDS
from brain.geometry import clear_polygon_cache
from brain.settings import settings

log = logging.getLogger("brain.config")

CAM_RE = re.compile(r"^cam-\d{2}$")

# Defaults from §4.3; a rule that omits a param gets the value from here.
DEFAULT_PARAMS: dict[str, dict[str, Any]] = {
    "fall": {"drop_rate": 1.0, "torso_angle_deg": 60, "aspect_min": 1.2,
             "still_s": 1.5, "still_max_disp": 0.02},
    "crowd": {"max_count": 15, "max_density": 2.5, "rate_per_s": 0.5},
    "crush": {"min_people": 4, "max_nn_dist_m": 0.5},
    "entry": {},
    "tripwire": {},
    "tailgate": {"window_s": 2.0},
    "loiter": {"dwell_s": 300},
    "running": {"speed_bh_s": 1.5, "sustain_s": 1.0},
    "fight": {"wrist_speed_bh_s": 2.0, "sustain_s": 1.0, "max_center_dist_bh": 0.6},
    "anomaly": {"consecutive_windows": 2},
}


def slugify(name: str) -> str:
    """`Server Room Door` -> `server-room-door`, for generated ids."""
    text = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "item"


class ConfigStore:
    """The three YAML files, reloaded on change and written atomically."""

    def __init__(self, config_dir: Path | None = None) -> None:
        self.dir = config_dir or settings.config_dir
        self._lock = threading.RLock()
        self._mtimes: dict[str, float] = {}
        self.cams: list[dict[str, Any]] = []
        self.zones: list[dict[str, Any]] = []
        self.lines: list[dict[str, Any]] = []
        self.rules: list[dict[str, Any]] = []
        self.reload(force=True)

    # --- paths ------------------------------------------------------------
    @property
    def cams_path(self) -> Path:
        return self.dir / "cams.yaml"

    @property
    def zones_path(self) -> Path:
        return self.dir / "zones.yaml"

    @property
    def rules_path(self) -> Path:
        return self.dir / "rules.yaml"

    # --- reading ----------------------------------------------------------
    def _read(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            log.warning("%s is missing — treating as empty", path.name)
            return {}
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            log.error("%s is not valid YAML, keeping the previous version: %s",
                      path.name, exc)
            raise
        return data if isinstance(data, dict) else {}

    def reload(self, force: bool = False) -> bool:
        """Re-read any file whose mtime moved. Returns True if anything changed."""
        changed = False
        with self._lock:
            for path, apply in (
                (self.cams_path, self._apply_cams),
                (self.zones_path, self._apply_zones),
                (self.rules_path, self._apply_rules),
            ):
                try:
                    mtime = path.stat().st_mtime if path.exists() else 0.0
                except OSError:
                    mtime = 0.0
                if not force and self._mtimes.get(path.name) == mtime:
                    continue
                try:
                    apply(self._read(path))
                except yaml.YAMLError:
                    continue  # keep the last good version; already logged
                self._mtimes[path.name] = mtime
                changed = True
            if changed:
                clear_polygon_cache()
                log.info("config loaded: %d cams, %d zones, %d lines, %d rules",
                         len(self.cams), len(self.zones), len(self.lines), len(self.rules))
        return changed

    def _apply_cams(self, data: dict[str, Any]) -> None:
        out = []
        for raw in data.get("cams") or []:
            cam = dict(raw)
            cam_id = str(cam.get("id", ""))
            if not CAM_RE.match(cam_id):
                log.warning("ignoring cam with invalid id %r (want cam-NN)", cam_id)
                continue
            cam.setdefault("name", cam_id)
            cam.setdefault("default_rect", [0, 0, 1600, 1000])
            cam.setdefault("homography", None)
            cam.setdefault("px_per_m", 40)
            out.append(cam)
        self.cams = out

    def _apply_zones(self, data: dict[str, Any]) -> None:
        zones = []
        for raw in data.get("zones") or []:
            zone = dict(raw)
            if not zone.get("id") or not zone.get("cam"):
                log.warning("ignoring zone without id/cam: %r", raw)
                continue
            zone["id"] = str(zone["id"])
            zone.setdefault("name", zone["id"])
            kind = str(zone.get("kind", "area"))
            if kind not in ZONE_KINDS:
                log.warning("zone %s has unknown kind %r, treating as area", zone["id"], kind)
                kind = "area"
            zone["kind"] = kind
            zone.setdefault("polygon", [])
            zone.setdefault("area_m2", 0)
            zones.append(zone)
        self.zones = zones

        lines = []
        for raw in data.get("lines") or []:
            line = dict(raw)
            if not line.get("id") or not line.get("cam"):
                log.warning("ignoring line without id/cam: %r", raw)
                continue
            line["id"] = str(line["id"])
            line.setdefault("name", line["id"])
            line.setdefault("kind", "tripwire")
            line.setdefault("a", [0.0, 0.5])
            line.setdefault("b", [1.0, 0.5])
            line.setdefault("direction", "any")
            lines.append(line)
        self.lines = lines

    def _apply_rules(self, data: dict[str, Any]) -> None:
        rules = []
        for raw in data.get("rules") or []:
            rule = dict(raw)
            if not rule.get("id") or not rule.get("type"):
                log.warning("ignoring rule without id/type: %r", raw)
                continue
            rule["id"] = str(rule["id"])
            rule["type"] = str(rule["type"])
            rule.setdefault("cam", "*")
            rule["enabled"] = bool(rule.get("enabled", True))
            severity = str(rule.get("severity", "medium"))
            if severity not in SEVERITIES:
                log.warning("rule %s has unknown severity %r, using medium",
                            rule["id"], severity)
                severity = "medium"
            rule["severity"] = severity
            rule["cooldown_s"] = float(rule.get("cooldown_s", 30))
            rule["params"] = {
                **DEFAULT_PARAMS.get(rule["type"], {}),
                **(rule.get("params") or {}),
            }
            rules.append(rule)
        self.rules = rules

    # --- lookups ----------------------------------------------------------
    def cam(self, cam_id: str) -> dict[str, Any] | None:
        with self._lock:
            return next((c for c in self.cams if c["id"] == cam_id), None)

    def cam_name(self, cam_id: str) -> str:
        cam = self.cam(cam_id)
        return str(cam["name"]) if cam else cam_id

    def zone(self, zone_id: str) -> dict[str, Any] | None:
        with self._lock:
            return next((z for z in self.zones if z["id"] == zone_id), None)

    def line(self, line_id: str) -> dict[str, Any] | None:
        with self._lock:
            return next((ln for ln in self.lines if ln["id"] == line_id), None)

    def rule(self, rule_id: str) -> dict[str, Any] | None:
        with self._lock:
            return next((r for r in self.rules if r["id"] == rule_id), None)

    def zones_for(self, cam_id: str, kind: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            return [z for z in self.zones
                    if z["cam"] == cam_id and (kind is None or z["kind"] == kind)]

    def cams_matching(self, pattern: str, active: list[str] | None = None) -> list[str]:
        """Resolve a rule's `cam` field. `*` means every currently active cam."""
        if pattern != "*":
            return [pattern]
        return list(active or [c["id"] for c in self.cams])

    # --- writing ----------------------------------------------------------
    def _write_atomic(self, path: Path, payload: dict[str, Any]) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100)
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
        with self._lock:
            try:
                self._mtimes[path.name] = path.stat().st_mtime
            except OSError:
                self._mtimes.pop(path.name, None)

    def save_cams(self) -> None:
        with self._lock:
            self._write_atomic(self.cams_path, {"cams": self.cams})

    def save_zones(self) -> None:
        with self._lock:
            self._write_atomic(self.zones_path, {"zones": self.zones, "lines": self.lines})
        clear_polygon_cache()

    def save_rules(self) -> None:
        with self._lock:
            self._write_atomic(self.rules_path, {"rules": self.rules})
