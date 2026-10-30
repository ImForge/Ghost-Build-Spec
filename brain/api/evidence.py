"""Evidence: upload from a node, 2-of-3 approval, and audited viewing."""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, Response, UploadFile,
)

from brain.api import get_brain
from brain.api.admin import require_role
from brain.escrow import EscrowError

log = logging.getLogger("brain.api.evidence")
router = APIRouter()

MAX_UPLOAD_BYTES = 256 * 1024 * 1024


@router.post("/evidence/upload")
async def upload(
    evidence_id: str = Form(...),
    cam: str = Form(...),
    nonce_hex: str = Form(...),
    tag_hex: str = Form(...),
    meta_json: str = Form(...),
    shares_json: str = Form(...),
    blob: UploadFile = File(...),
    brain=Depends(get_brain),
) -> dict[str, Any]:
    """Accept a sealed clip from a node.

    Unauthenticated on purpose: a node has no keyholder role, and what it is
    handing over is ciphertext the brain cannot read. The access control that
    matters is on the way out, not the way in.
    """
    data = await blob.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "sealed clip is too large")
    try:
        shares = json.loads(shares_json)
    except ValueError as exc:
        raise HTTPException(422, f"shares_json is not valid JSON: {exc}") from exc
    if not isinstance(shares, list):
        raise HTTPException(422, "shares_json must be a list of [index, hex] pairs")

    try:
        return brain.escrow.store_upload(
            evidence_id=evidence_id, cam=cam, nonce_hex=nonce_hex, tag_hex=tag_hex,
            meta_json=meta_json, shares=shares, blob=data,
        )
    except EscrowError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/evidence/{evidence_id}")
def get_evidence(evidence_id: str, brain=Depends(get_brain)) -> dict[str, Any]:
    """Status, metadata and which roles have approved. Never the key shares."""
    summary = brain.escrow.summary(evidence_id)
    if summary is None:
        raise HTTPException(404, f"no such evidence: {evidence_id}")
    return summary


@router.post("/evidence/{evidence_id}/approve")
def approve(evidence_id: str, role: str = Depends(require_role),
            brain=Depends(get_brain)) -> dict[str, Any]:
    try:
        return brain.escrow.approve(evidence_id, role)
    except EscrowError as exc:
        raise HTTPException(404 if "no such" in str(exc) else 409, str(exc)) from exc


@router.post("/evidence/{evidence_id}/flag")
def flag(evidence_id: str, role: str = Depends(require_role),
         brain=Depends(get_brain)) -> dict[str, Any]:
    try:
        return brain.escrow.flag(evidence_id, role)
    except EscrowError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/evidence/{evidence_id}/clip")
def clip(evidence_id: str, role: str = Depends(require_role),
         brain=Depends(get_brain)) -> Response:
    """The decrypted ZIP of frames. Only while unsealed, and always audited."""
    try:
        payload = brain.escrow.clip(evidence_id, role)
    except EscrowError as exc:
        raise HTTPException(404 if "no such" in str(exc) else 409, str(exc)) from exc
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{evidence_id}.zip"'},
    )
