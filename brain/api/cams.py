"""Camera configuration, snapshots and floor calibration."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from brain.api import get_brain
from brain.geometry import homography_from_points

log = logging.getLogger("brain.api.cams")
router = APIRouter()


class CamUpdate(BaseModel):
    name: str | None = None
    default_rect: list[float] | None = Field(None, min_length=4, max_length=4)
    px_per_m: float | None = Field(None, gt=0)


class HomographyBody(BaseModel):
    cam_pts: list[list[float]] = Field(min_length=4, max_length=4)
    floor_pts: list[list[float]] = Field(min_length=4, max_length=4)


@router.get("/cams")
def list_cams(brain=Depends(get_brain)) -> list[dict[str, Any]]:
    """Configured cameras with their live health merged in."""
    live = {c["cam"]: c for c in brain.world.snapshot_summary()}
    out = []
    for cam in brain.config.cams:
        seen = live.get(cam["id"], {})
        out.append({
            **cam,
            "calibrated": cam.get("homography") is not None,
            "online": bool(seen.get("online", False)),
            "active": bool(seen.get("active", False)),
            "count": int(seen.get("count", 0)),
            "learning": bool(seen.get("learning", False)),
            "health": seen.get("health", {}),
        })
    return out


def _require_cam(brain: Any, cam_id: str) -> dict[str, Any]:
    cam = brain.config.cam(cam_id)
    if cam is None:
        raise HTTPException(404, f"no such cam: {cam_id}")
    return cam


@router.put("/cams/{cam_id}")
def update_cam(cam_id: str, body: CamUpdate, brain=Depends(get_brain)) -> dict[str, Any]:
    cam = _require_cam(brain, cam_id)
    changes = body.model_dump(exclude_none=True)
    cam.update(changes)
    brain.config.save_cams()
    brain.db.audit("api", "config_change", f"cam:{cam_id}", changes)
    return cam


@router.post("/cams/{cam_id}/snapshot")
def request_snapshot(cam_id: str, brain=Depends(get_brain)) -> Response:
    """Ask the node for one pixelated still and return it as an image."""
    _require_cam(brain, cam_id)
    jpg = brain.request_snapshot(cam_id)
    if jpg is None:
        raise HTTPException(
            504, f"{cam_id} did not answer the snapshot request within 3s"
        )
    return Response(content=jpg, media_type="image/jpeg")


@router.get("/cams/{cam_id}/snapshot")
def cached_snapshot(cam_id: str, brain=Depends(get_brain)) -> Response:
    state = brain.world.cams.get(cam_id)
    jpg = state.snapshot_jpg if state else None
    if not jpg:
        raise HTTPException(404, f"no snapshot cached for {cam_id}")
    return Response(content=jpg, media_type="image/jpeg")


@router.post("/cams/{cam_id}/homography")
def set_homography(cam_id: str, body: HomographyBody,
                   brain=Depends(get_brain)) -> dict[str, Any]:
    cam = _require_cam(brain, cam_id)
    try:
        matrix = homography_from_points(body.cam_pts, body.floor_pts)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    cam["homography"] = matrix
    brain.config.save_cams()
    brain.db.audit("api", "config_change", f"cam:{cam_id}", "homography set")
    log.info("[%s] calibrated", cam_id)
    return {"cam": cam_id, "calibrated": True, "homography": matrix}


@router.delete("/cams/{cam_id}/homography")
def clear_homography(cam_id: str, brain=Depends(get_brain)) -> dict[str, Any]:
    cam = _require_cam(brain, cam_id)
    cam["homography"] = None
    brain.config.save_cams()
    brain.db.audit("api", "config_change", f"cam:{cam_id}", "homography cleared")
    return {"cam": cam_id, "calibrated": False}
