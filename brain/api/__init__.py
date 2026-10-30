"""REST layer. Routers get at the running brain through `get_brain`."""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException, Request

if TYPE_CHECKING:  # pragma: no cover
    from brain.app import Brain


def get_brain(request: Request) -> Brain:
    brain = getattr(request.app.state, "brain", None)
    if brain is None:  # pragma: no cover — only before startup completes
        raise HTTPException(503, "brain is still starting")
    return brain
