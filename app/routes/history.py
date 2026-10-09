from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException

from app.config import settings
from app.services.history import history_service

router = APIRouter()


def require_admin(authorization: str | None = Header(default=None)) -> None:
    if not settings.admin_token:
        return
    if authorization != f"Bearer {settings.admin_token}":
        raise HTTPException(status_code=401, detail="未授权")


@router.get("/api/history/devices")
async def devices(_: None = Depends(require_admin)):
    return history_service.devices()


@router.get("/api/history/sessions")
async def sessions(sn: str | None = None, keyword: str | None = None, _: None = Depends(require_admin)):
    return history_service.sessions(sn=sn, keyword=keyword)


@router.get("/api/history/sessions/{session_id}")
async def session_detail(session_id: str, _: None = Depends(require_admin)):
    rows = history_service.session_detail(session_id)
    if not rows:
        raise HTTPException(status_code=404, detail="会话不存在")
    return rows


@router.get("/api/history/sessions/{session_id}/export")
async def session_export(session_id: str, _: None = Depends(require_admin)):
    rows = history_service.session_detail(session_id)
    if not rows:
        raise HTTPException(status_code=404, detail="会话不存在")
    return rows
