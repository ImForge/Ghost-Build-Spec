"""Alert history, acknowledgement and the per-alert snapshot."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from brain.api import get_brain
from brain.constants import ALERT_STATUSES, ALERT_TYPES
from brain.settings import settings

log = logging.getLogger("brain.api.alerts")
router = APIRouter()


@router.get("/alerts")
def list_alerts(
    status: str | None = None,
    type: str | None = None,  # noqa: A002 — the query parameter is named in §7.11
    cam: str | None = None,
    since: float | None = None,
    limit: int = Query(100, ge=1, le=1000),
    brain=Depends(get_brain),
) -> list[dict[str, Any]]:
    """Newest first. Filters match the dashboard's feed controls."""
    if status and status not in ALERT_STATUSES:
        raise HTTPException(422, f"status must be one of {ALERT_STATUSES}")
    if type and type not in ALERT_TYPES:
        raise HTTPException(422, f"type must be one of {ALERT_TYPES}")
    rows = brain.db.list_alerts(status=status, type_=type, cam=cam, since=since, limit=limit)
    return [_decorate(brain, row) for row in rows]


@router.get("/alerts/{alert_id}")
def get_alert(alert_id: str, brain=Depends(get_brain)) -> dict[str, Any]:
    alert = brain.db.get_alert(alert_id)
    if alert is None:
        raise HTTPException(404, f"no such alert: {alert_id}")
    return _decorate(brain, alert)


@router.post("/alerts/{alert_id}/ack")
def ack_alert(alert_id: str, brain=Depends(get_brain)) -> dict[str, Any]:
    return _set_status(brain, alert_id, "acked", "ack")


@router.post("/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: str, brain=Depends(get_brain)) -> dict[str, Any]:
    return _set_status(brain, alert_id, "resolved", "resolve")


@router.get("/alerts/{alert_id}/snapshot")
def alert_snapshot(alert_id: str, brain=Depends(get_brain)) -> Response:
    """The pixelated still captured when this alert fired, if the node answered."""
    if brain.db.get_alert(alert_id) is None:
        raise HTTPException(404, f"no such alert: {alert_id}")
    path = _snapshot_path(alert_id)
    if not path.exists():
        raise HTTPException(404, f"no snapshot stored for {alert_id}")
    return Response(content=path.read_bytes(), media_type="image/jpeg")


def _snapshot_path(alert_id: str) -> Path:
    # Guard the path join: alert_id reaches us straight from the URL.
    safe = "".join(ch for ch in alert_id if ch.isalnum() or ch in "_-")
    return settings.snapshot_dir / f"{safe}.jpg"


def _set_status(brain: Any, alert_id: str, status: str, action: str) -> dict[str, Any]:
    alert = brain.db.get_alert(alert_id)
    if alert is None:
        raise HTTPException(404, f"no such alert: {alert_id}")
    brain.db.update_alert(alert_id, status=status)
    brain.db.audit("api", action, alert_id, {"from": alert["status"], "to": status})
    updated = brain.alerts.republish(alert_id) or {**alert, "status": status}
    return _decorate(brain, updated)


def _decorate(brain: Any, alert: dict[str, Any]) -> dict[str, Any]:
    """Add the display fields the dashboard would otherwise have to join itself."""
    return {
        **alert,
        "cam_name": brain.config.cam_name(str(alert["cam"])),
        "has_snapshot": _snapshot_path(str(alert["id"])).exists(),
    }
