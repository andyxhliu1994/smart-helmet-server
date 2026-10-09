from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, UploadFile, File

from app.config import settings
from app.services.knowledge import knowledge_service

router = APIRouter()


def require_admin(authorization: str | None = Header(default=None)) -> None:
    if not settings.admin_token:
        return
    if authorization != f"Bearer {settings.admin_token}":
        raise HTTPException(status_code=401, detail="未授权")


@router.get("/api/knowledge/flows")
async def list_flows(_: None = Depends(require_admin)):
    return knowledge_service.list_flows()


@router.post("/api/knowledge/flows")
async def create_flow(payload: dict, _: None = Depends(require_admin)):
    try:
        return knowledge_service.create_flow(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/knowledge/flows/{flow_id}")
async def get_flow(flow_id: str, _: None = Depends(require_admin)):
    flow = knowledge_service.get_flow(flow_id)
    if flow is None:
        raise HTTPException(status_code=404, detail="流程不存在")
    return flow


@router.put("/api/knowledge/flows/{flow_id}")
async def update_flow(flow_id: str, payload: dict, _: None = Depends(require_admin)):
    try:
        return knowledge_service.update_flow(flow_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="流程不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/knowledge/flows/{flow_id}")
async def delete_flow(flow_id: str, _: None = Depends(require_admin)):
    try:
        knowledge_service.delete_flow(flow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="流程不存在") from exc
    return {"ok": True}


@router.post("/api/knowledge/reload")
async def reload_flows(_: None = Depends(require_admin)):
    return knowledge_service.reload()


@router.post("/api/knowledge/import-docx")
async def import_docx(file: UploadFile = File(...), _: None = Depends(require_admin)):
    data = await file.read()
    text = data.decode("utf-8", errors="ignore")
    return knowledge_service.import_docx_draft(text)
