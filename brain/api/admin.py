"""Audit trail and operational endpoints.

Role authentication and the evidence debug hook are added with the escrow work.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query

from brain.api import get_brain

log = logging.getLogger("brain.api.admin")
router = APIRouter()


@router.get("/audit")
def audit(limit: int = Query(200, ge=1, le=2000), target: str | None = None,
          brain=Depends(get_brain)) -> list[dict[str, Any]]:
    """Every consequential action, newest first. Viewing evidence lands here too."""
    return brain.db.list_audit(limit=limit, target=target)
