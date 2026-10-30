"""Audit trail, role identification, and operational endpoints.

The three role tokens are the only access control in the system. They do not
protect the dashboard or the configuration — anyone on the LAN can see those.
They exist for one purpose: no single person can unseal evidence alone (§7.9).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from brain.api import get_brain
from brain.constants import KEYHOLDER_ROLES
from brain.settings import settings

log = logging.getLogger("brain.api.admin")
router = APIRouter()


def resolve_role(token: str | None) -> str | None:
    """Map a token to its keyholder role, or None."""
    if not token:
        return None
    return settings.role_tokens.get(token.strip())


def require_role(x_role_token: str | None = Header(default=None)) -> str:
    """FastAPI dependency: a valid keyholder role, or 401.

    Used only on the evidence endpoints. An unknown token and a missing one get
    the same answer, so a wrong guess learns nothing.
    """
    role = resolve_role(x_role_token)
    if role is None:
        raise HTTPException(401, "a valid X-Role-Token header is required")
    return role


@router.post("/auth/whoami")
def whoami(role: str = Depends(require_role)) -> dict[str, Any]:
    return {"role": role, "keyholder_roles": list(KEYHOLDER_ROLES)}


@router.post("/debug/seal/{cam}")
def debug_seal(cam: str, brain=Depends(get_brain)) -> dict[str, Any]:
    """Trigger a seal without waiting for a real incident. DEBUG=1 only.

    Sealing is normally driven by an alert above the severity floor, which is
    hard to arrange on demand with a test video that contains no people.
    """
    if not settings.debug:
        raise HTTPException(404, "not found")
    if brain.config.cam(cam) is None:
        raise HTTPException(404, f"no such cam: {cam}")
    evidence_id = brain.escrow.request_seal(
        {"id": None, "cam": cam, "severity": "critical"}
    )
    return {"evidence_id": evidence_id, "cam": cam, "status": "requested"}


@router.get("/audit")
def audit(limit: int = Query(200, ge=1, le=2000), target: str | None = None,
          brain=Depends(get_brain)) -> list[dict[str, Any]]:
    """Every consequential action, newest first. Viewing evidence lands here too."""
    return brain.db.list_audit(limit=limit, target=target)
