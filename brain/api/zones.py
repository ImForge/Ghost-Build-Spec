"""Zones and lines: the map the rules are written against."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from brain.api import get_brain
from brain.config import slugify
from brain.constants import LINE_DIRECTIONS, LINE_KINDS, ZONE_KINDS

log = logging.getLogger("brain.api.zones")
router = APIRouter()


class ZoneBody(BaseModel):
    id: str | None = None
    cam: str
    name: str
    kind: str = "area"
    polygon: list[list[float]] = Field(default_factory=list)
    area_m2: float = 0.0

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in ZONE_KINDS:
            raise ValueError(f"kind must be one of {ZONE_KINDS}")
        return v

    @field_validator("polygon")
    @classmethod
    def _polygon(cls, v: list[list[float]]) -> list[list[float]]:
        if v and len(v) < 3:
            raise ValueError("a polygon needs at least 3 points")
        for point in v:
            if len(point) != 2:
                raise ValueError("each polygon point is [x, y]")
        return v


class ZonePatch(BaseModel):
    cam: str | None = None
    name: str | None = None
    kind: str | None = None
    polygon: list[list[float]] | None = None
    area_m2: float | None = None


class LineBody(BaseModel):
    id: str | None = None
    cam: str
    name: str
    kind: str = "tripwire"
    a: list[float] = Field(min_length=2, max_length=2)
    b: list[float] = Field(min_length=2, max_length=2)
    direction: str = "any"

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in LINE_KINDS:
            raise ValueError(f"kind must be one of {LINE_KINDS}")
        return v

    @field_validator("direction")
    @classmethod
    def _direction(cls, v: str) -> str:
        if v not in LINE_DIRECTIONS:
            raise ValueError(f"direction must be one of {LINE_DIRECTIONS}")
        return v


class LinePatch(BaseModel):
    cam: str | None = None
    name: str | None = None
    kind: str | None = None
    a: list[float] | None = None
    b: list[float] | None = None
    direction: str | None = None


def _unique_id(existing: list[dict[str, Any]], wanted: str) -> str:
    """Keep generated slugs unique: bench, bench-2, bench-3..."""
    taken = {item["id"] for item in existing}
    if wanted not in taken:
        return wanted
    n = 2
    while f"{wanted}-{n}" in taken:
        n += 1
    return f"{wanted}-{n}"


def _check_cam(brain: Any, cam_id: str) -> None:
    if brain.config.cam(cam_id) is None:
        raise HTTPException(422, f"no such cam: {cam_id}")


# --- zones --------------------------------------------------------------
@router.get("/zones")
def list_zones(cam: str | None = None, brain=Depends(get_brain)) -> list[dict[str, Any]]:
    zones = brain.config.zones
    return [z for z in zones if cam is None or z["cam"] == cam]


@router.post("/zones", status_code=201)
def create_zone(body: ZoneBody, brain=Depends(get_brain)) -> dict[str, Any]:
    _check_cam(brain, body.cam)
    zone = body.model_dump()
    zone["id"] = str(zone["id"] or _unique_id(brain.config.zones, slugify(body.name)))
    if brain.config.zone(zone["id"]):
        raise HTTPException(409, f"zone {zone['id']} already exists")
    brain.config.zones.append(zone)
    brain.config.save_zones()
    brain.db.audit("api", "config_change", f"zone:{zone['id']}", "created")
    return zone


@router.put("/zones/{zone_id}")
def update_zone(zone_id: str, body: ZonePatch, brain=Depends(get_brain)) -> dict[str, Any]:
    zone = brain.config.zone(zone_id)
    if zone is None:
        raise HTTPException(404, f"no such zone: {zone_id}")
    changes = body.model_dump(exclude_none=True)
    if "cam" in changes:
        _check_cam(brain, changes["cam"])
    if "kind" in changes and changes["kind"] not in ZONE_KINDS:
        raise HTTPException(422, f"kind must be one of {ZONE_KINDS}")
    zone.update(changes)
    brain.config.save_zones()
    brain.db.audit("api", "config_change", f"zone:{zone_id}", changes)
    return zone


@router.delete("/zones/{zone_id}")
def delete_zone(zone_id: str, brain=Depends(get_brain)) -> dict[str, Any]:
    zone = brain.config.zone(zone_id)
    if zone is None:
        raise HTTPException(404, f"no such zone: {zone_id}")
    brain.config.zones.remove(zone)
    brain.config.save_zones()
    brain.db.audit("api", "config_change", f"zone:{zone_id}", "deleted")
    return {"deleted": zone_id}


# --- lines --------------------------------------------------------------
@router.get("/lines")
def list_lines(cam: str | None = None, brain=Depends(get_brain)) -> list[dict[str, Any]]:
    return [ln for ln in brain.config.lines if cam is None or ln["cam"] == cam]


@router.post("/lines", status_code=201)
def create_line(body: LineBody, brain=Depends(get_brain)) -> dict[str, Any]:
    _check_cam(brain, body.cam)
    line = body.model_dump()
    line["id"] = str(line["id"] or _unique_id(brain.config.lines, slugify(body.name)))
    if brain.config.line(line["id"]):
        raise HTTPException(409, f"line {line['id']} already exists")
    brain.config.lines.append(line)
    brain.config.save_zones()
    brain.db.audit("api", "config_change", f"line:{line['id']}", "created")
    return line


@router.put("/lines/{line_id}")
def update_line(line_id: str, body: LinePatch, brain=Depends(get_brain)) -> dict[str, Any]:
    line = brain.config.line(line_id)
    if line is None:
        raise HTTPException(404, f"no such line: {line_id}")
    changes = body.model_dump(exclude_none=True)
    if "cam" in changes:
        _check_cam(brain, changes["cam"])
    if "direction" in changes and changes["direction"] not in LINE_DIRECTIONS:
        raise HTTPException(422, f"direction must be one of {LINE_DIRECTIONS}")
    line.update(changes)
    brain.config.save_zones()
    brain.db.audit("api", "config_change", f"line:{line_id}", changes)
    return line


@router.delete("/lines/{line_id}")
def delete_line(line_id: str, brain=Depends(get_brain)) -> dict[str, Any]:
    line = brain.config.line(line_id)
    if line is None:
        raise HTTPException(404, f"no such line: {line_id}")
    brain.config.lines.remove(line)
    brain.config.save_zones()
    brain.db.audit("api", "config_change", f"line:{line_id}", "deleted")
    return {"deleted": line_id}
