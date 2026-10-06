from __future__ import annotations

from fastapi import APIRouter

from app.services.dashboard_service import summarize
from app.state.store import get_store

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary")
def dashboard_summary() -> dict:
    return summarize(get_store())
