"""Health and the aggregate stats the PrivacyMeter is built on."""
from __future__ import annotations

import logging
import time
from typing import Any

import requests
from fastapi import APIRouter, Depends

from brain.api import get_brain
from brain.constants import PROJECT_NAME
from brain.settings import settings

log = logging.getLogger("brain.api.health")
router = APIRouter()

_ollama_cache: dict[str, Any] = {"at": 0.0, "up": False}
OLLAMA_CACHE_S = 60.0


def ollama_reachable() -> bool:
    """Cached for a minute — this is polled by the dashboard, not by a human."""
    now = time.time()
    if now - float(_ollama_cache["at"]) < OLLAMA_CACHE_S:
        return bool(_ollama_cache["up"])
    up = False
    try:
        resp = requests.get(f"{settings.ollama_url}/api/tags", timeout=1.5)
        up = resp.status_code == 200
    except requests.RequestException:
        up = False
    _ollama_cache.update(at=now, up=up)
    return up


@router.get("/health")
def health(brain=Depends(get_brain)) -> dict[str, Any]:
    cams = brain.world.snapshot_summary()
    return {
        "project": PROJECT_NAME,
        "ok": True,
        "up_s": round(time.time() - brain.world.started, 1),
        "mqtt_connected": brain.bus.connected.is_set(),
        "engine_ticks": brain.engine.ticks,
        "ollama": ollama_reachable(),
        "cams": cams,
        "cams_total": len(brain.config.cams),
        "cams_online": sum(1 for c in cams if c["online"]),
    }


@router.get("/stats")
def stats(brain=Depends(get_brain)) -> dict[str, Any]:
    """The privacy numbers: how many bytes the cameras are actually emitting."""
    cams = brain.world.snapshot_summary()
    online = [c for c in cams if c["online"]]
    return {
        "bytes_out_per_s": sum(int(c["health"].get("bytes_out_per_s", 0)) for c in online),
        "cams_online": len(online),
        "cams_total": len(brain.config.cams),
        "people_now": sum(int(c["count"]) for c in online),
        "alerts_open": brain.db.count_open_alerts(),
        "pixels_out": 0,
    }
