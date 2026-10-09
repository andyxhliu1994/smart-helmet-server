from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.config import settings
from app.services.audio import AudioConversionError, audio_service
from app.services.logger import logger
from app.services.safe_tts_delivery import safe_tts_delivery_service
from app.services.tts import tts_service

router = APIRouter(prefix="/api/safe-tts", tags=["safe-tts"])


def require_admin(authorization: str | None = Header(default=None)) -> None:
    if not settings.admin_token:
        return
    if authorization != f"Bearer {settings.admin_token}":
        raise HTTPException(status_code=401, detail="未授权")


class SafeTtsTextRequest(BaseModel):
    sn: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)
    interrupt_current: bool = True
    send_control_messages: bool = True
    flush_before_start: bool | None = None
    reason: str = "api_safe_tts_text"


class SafeTtsCancelRequest(BaseModel):
    sn: str = Field(..., min_length=1)
    reason: str = "api_safe_tts_cancel"
    flush_silence: bool = True
    send_control: bool = True


class SafeTtsGatewayRequest(BaseModel):
    sn: str = Field(..., min_length=1)
    text: str = Field(default="你好，这是一次安全 TTS 下发测试。", min_length=1)
    url: str = Field(default=settings.tts_gateway_url, min_length=1)
    token: str = ""
    response_format: str = "pcm"
    verify_ssl: bool = True
    interrupt_current: bool = True
    send_control_messages: bool = True
    flush_before_start: bool | None = None
    reason: str = "api_safe_tts_gateway"


@router.get("/status/{sn}")
async def safe_tts_status(sn: str, _: None = Depends(require_admin)) -> dict[str, Any]:
    return await safe_tts_delivery_service.status(sn)


@router.post("/cancel")
async def safe_tts_cancel(payload: SafeTtsCancelRequest, _: None = Depends(require_admin)) -> dict[str, Any]:
    result = await safe_tts_delivery_service.cancel(
        payload.sn,
        reason=payload.reason or "api_safe_tts_cancel",
        send_control=payload.send_control,
        flush_silence=payload.flush_silence,
    )
    return result.as_dict()


@router.post("/send-text")
async def safe_tts_send_text(payload: SafeTtsTextRequest, _: None = Depends(require_admin)) -> dict[str, Any]:
    logger.info("[安全TTS接口] send-text sn=%s text=%s", payload.sn, payload.text[:40])
    result = await safe_tts_delivery_service.send_text(
        payload.sn,
        payload.text,
        reason=payload.reason or "api_safe_tts_text",
        interrupt_current=payload.interrupt_current,
        send_control_messages=payload.send_control_messages,
        flush_before_start=payload.flush_before_start,
    )
    return result.as_dict()


@router.post("/send-pcm")
async def safe_tts_send_pcm(
    sn: str = Form(...),
    file: UploadFile = File(...),
    text: str = Form(""),
    interrupt_current: bool = Form(True),
    send_control_messages: bool = Form(True),
    reason: str = Form("api_safe_tts_pcm"),
    _: None = Depends(require_admin),
) -> dict[str, Any]:
    raw = await file.read()
    filename = file.filename or "audio.pcm"
    try:
        pcm = audio_service.pcm_from_upload_bytes(filename, raw)
    except AudioConversionError as exc:
        raise HTTPException(status_code=400, detail=f"音频转换失败：{exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"音频处理失败：{exc}") from exc

    if not pcm:
        raise HTTPException(status_code=400, detail="PCM 为空")

    result = await safe_tts_delivery_service.send_pcm(
        sn,
        pcm,
        text=text or filename,
        reason=reason or "api_safe_tts_pcm",
        interrupt_current=interrupt_current,
        send_control_messages=send_control_messages,
    )
    data = result.as_dict()
    data["source_file"] = filename
    data["source_bytes"] = len(raw)
    data["pcm_bytes"] = len(pcm)
    return data


@router.post("/send-gateway")
async def safe_tts_send_gateway(payload: SafeTtsGatewayRequest, _: None = Depends(require_admin)) -> dict[str, Any]:
    logger.info("[安全TTS接口] send-gateway sn=%s url=%s", payload.sn, payload.url)
    pcm = await tts_service.synthesize_pcm_via_gateway(
        text=payload.text,
        url=payload.url,
        token=payload.token,
        response_format=payload.response_format,
        verify_ssl=payload.verify_ssl,
    )
    result = await safe_tts_delivery_service.send_pcm(
        payload.sn,
        pcm,
        text=payload.text,
        reason=payload.reason or "api_safe_tts_gateway",
        interrupt_current=payload.interrupt_current,
        send_control_messages=payload.send_control_messages,
        flush_before_start=payload.flush_before_start,
    )
    data = result.as_dict()
    data["gateway_bytes"] = len(pcm)
    return data
