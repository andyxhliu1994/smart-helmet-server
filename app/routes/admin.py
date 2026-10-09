from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.config import settings
from app.services.audio import AudioConversionError, audio_service
from app.services.broadcaster import broadcaster
from app.services.llm import llm_service
from app.services.logger import logger
from app.services.tts import tts_service

router = APIRouter(prefix="/api/admin", tags=["admin"])

DEFAULT_TTS_TEST_URL = settings.tts_gateway_url


class AdminTtsRequest(BaseModel):
    sn: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)
    stream_to_web: bool = True
    chunk_chars: int = Field(default=8, ge=1, le=80)
    interval_ms: int = Field(default=60, ge=0, le=2000)
    interrupt_current: bool = True
    send_control_messages: bool = True


class AdminCancelRequest(BaseModel):
    sn: str = Field(..., min_length=1)
    reason: str = "admin_cancel"


class AdminDisconnectRequest(BaseModel):
    sn: str = Field(..., min_length=1)
    reason: str = "admin_disconnect"


class AdminRestartServiceRequest(BaseModel):
    reason: str = "admin_restart"


class AdminCmdRequest(BaseModel):
    sn: str = Field(..., min_length=1)
    cmd: str = Field(..., min_length=1)
    msgid: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class AdminBroadcastCmdRequest(BaseModel):
    cmd: str = Field(..., min_length=1)
    msgid: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class AdminTtsGatewayTestRequest(BaseModel):
    text: str = Field(default="你好，这是一次语音合成测试。", min_length=1)
    url: str = Field(default=DEFAULT_TTS_TEST_URL, min_length=1)
    token: str = ""
    response_format: str = "pcm"
    verify_ssl: bool = True
    send_to_device: bool = False
    sn: str | None = None
    interrupt_current: bool = True
    send_control_messages: bool = True


class AdminLlmTtsGatewayRequest(BaseModel):
    sn: str = Field(..., min_length=1)
    prompt: str = Field(..., min_length=1)
    url: str = Field(default=DEFAULT_TTS_TEST_URL, min_length=1)
    token: str = ""
    response_format: str = "pcm"
    verify_ssl: bool = True
    max_sentence_chars: int = Field(default=36, ge=8, le=120)
    stream_chunk_chars: int = Field(default=8, ge=1, le=80)
    stream_interval_ms: int = Field(default=60, ge=0, le=2000)
    interrupt_current: bool = True
    send_control_messages: bool = True


COMMAND_MSGIDS = {
    "start_pcm": 11,
    "stop_pcm": 12,
    "start_h264": 21,
    "stop_h264": 22,
    "snap_jpeg": 33,
    "reboot": 44,
    "uptime": 55,
}

_service_restart_requested = False


def require_admin(authorization: str | None = Header(default=None)) -> None:
    if not settings.admin_token:
        return
    if authorization != f"Bearer {settings.admin_token}":
        raise HTTPException(status_code=401, detail="未授权")


@router.get("/clients")
async def clients(include_offline: bool = False, _: None = Depends(require_admin)) -> dict[str, Any]:
    rows = []
    for sn, session in sorted(broadcaster.sessions.items()):
        is_online = session.cmd_ws is not None or session.voice_ws is not None or bool(session.web_clients)
        if not include_offline and not is_online:
            continue
        rows.append({
            "sn": sn,
            "cmd_connected": session.cmd_ws is not None,
            "voice_connected": session.voice_ws is not None,
            "web_clients": len(session.web_clients),
            "online": is_online,
            "current_task": session.current_task,
            "task_state": session.task_state,
            "latest_image": session.latest_image,
            "last_agent_reply": session.last_agent_reply[:80],
        })
    return {"clients": rows}


@router.post("/cleanup-cmd-disconnected")
async def cleanup_cmd_disconnected(_: None = Depends(require_admin)) -> dict[str, Any]:
    removed = await broadcaster.remove_sessions_without_cmd()
    logger.info("[admin] cleanup cmd-disconnected clients: %s", removed)
    return {"ok": True, "count": len(removed), "removed": removed}


@router.post("/disconnect-client")
async def disconnect_client(payload: AdminDisconnectRequest, _: None = Depends(require_admin)) -> dict[str, Any]:
    result = await broadcaster.disconnect_client(payload.sn, payload.reason or "admin_disconnect")
    if result is None:
        raise HTTPException(status_code=404, detail=f"设备 {payload.sn} 不存在或已断开")
    return {"ok": True, **result}


@router.post("/restart-service")
async def restart_service(
    payload: AdminRestartServiceRequest,
    background_tasks: BackgroundTasks,
    _: None = Depends(require_admin),
) -> dict[str, Any]:
    global _service_restart_requested
    reason = payload.reason or "admin_restart"
    if _service_restart_requested:
        return {"ok": True, "pending": True, "pid": os.getpid(), "message": "服务重启已在执行"}

    _service_restart_requested = True
    background_tasks.add_task(_restart_service_after_response, reason)
    logger.warning("[管理端] 收到服务重启请求：pid=%s reason=%s", os.getpid(), reason)
    return {"ok": True, "pending": True, "pid": os.getpid(), "message": "服务重启请求已提交"}


@router.post("/send-pcm")
async def send_pcm(
    sn: str = Form(...),
    file: UploadFile = File(...),
    interrupt_current: bool = Form(True),
    send_control_messages: bool = Form(True),
    _: None = Depends(require_admin),
) -> dict[str, Any]:
    _ensure_voice_connected(sn)
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="音频文件为空")
    max_size = settings.max_pcm_size_mb * 1024 * 1024
    if len(data) > max_size:
        raise HTTPException(status_code=413, detail=f"音频文件超过限制：{settings.max_pcm_size_mb}MB")

    try:
        pcm = audio_service.pcm_from_upload_bytes(file.filename, data)
    except AudioConversionError as exc:
        logger.error("[管理端] MP3 转 PCM 失败：%s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if interrupt_current:
        await broadcaster.cancel_tts(sn, "admin_send_pcm")
    await broadcaster.publish(sn, {
        "type": "admin_send_pcm",
        "file": file.filename,
        "bytes": len(pcm),
        "message": "管理端主动下发音频。",
    }, persist=False)
    await broadcaster.send_voice(
        sn,
        pcm,
        f"管理端下发音频：{file.filename or 'uploaded.audio'}",
        send_control_messages=send_control_messages,
    )
    logger.info("[管理端] 已向设备 %s 下发音频：file=%s bytes=%s", sn, file.filename, len(pcm))
    return {"ok": True, "sn": sn, "bytes": len(pcm), "send_control_messages": send_control_messages}


@router.post("/send-tts")
async def send_tts(payload: AdminTtsRequest, _: None = Depends(require_admin)) -> dict[str, Any]:
    _ensure_voice_connected(payload.sn)
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="文字内容为空")

    if payload.interrupt_current:
        await broadcaster.cancel_tts(payload.sn, "admin_send_tts")

    message_id = f"admin-tts-{int(time.time() * 1000)}"
    if payload.stream_to_web:
        await _publish_streaming_text(
            payload.sn,
            text,
            message_id,
            payload.chunk_chars,
            payload.interval_ms / 1000,
        )

    pcm = await tts_service.synthesize_pcm(text)
    await broadcaster.publish(payload.sn, {
        "type": "admin_send_tts",
        "message_id": message_id,
        "text": text,
        "bytes": len(pcm),
        "message": "管理端已将文字合成为语音并下发。",
    }, persist=False)
    await broadcaster.send_voice(payload.sn, pcm, text, send_control_messages=payload.send_control_messages)
    logger.info("[管理端] 已向设备 %s 下发文字转语音：chars=%s bytes=%s", payload.sn, len(text), len(pcm))
    return {
        "ok": True,
        "sn": payload.sn,
        "bytes": len(pcm),
        "message_id": message_id,
        "send_control_messages": payload.send_control_messages,
    }


@router.post("/test-tts-gateway")
async def test_tts_gateway(payload: AdminTtsGatewayTestRequest, _: None = Depends(require_admin)) -> dict[str, Any]:
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="文字内容为空")
    if payload.send_to_device:
        if not payload.sn:
            raise HTTPException(status_code=400, detail="下发到设备时必须选择 sn")
        _ensure_voice_connected(payload.sn)

    sentences = tts_service.split_sentences(text)
    pcm_segments: list[bytes] = []
    try:
        for sentence in sentences:
            pcm_segments.append(await tts_service.synthesize_pcm_via_gateway(
                text=sentence,
                url=payload.url,
                token=payload.token.strip(),
                response_format=payload.response_format,
                verify_ssl=payload.verify_ssl,
            ))
        pcm = tts_service.join_pcm_segments(pcm_segments)
    except Exception as exc:
        logger.error("[管理端] TTS 测试服务调用失败：%s", exc)
        raise HTTPException(status_code=502, detail=f"TTS 测试服务调用失败：{exc}") from exc

    filename = f"admin-tts-test-{int(time.time() * 1000)}.pcm"
    target = settings.upload_dir / "tts" / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(pcm)

    if payload.send_to_device and payload.sn:
        if payload.interrupt_current:
            await broadcaster.cancel_tts(payload.sn, "admin_test_tts_gateway")
        await broadcaster.publish(payload.sn, {
            "type": "admin_test_tts_gateway",
            "text": text,
            "bytes": len(pcm),
            "file": filename,
            "message": "管理端已调用测试 TTS 服务并下发语音。",
        }, persist=False)
        await broadcaster.send_voice(payload.sn, pcm, text, send_control_messages=payload.send_control_messages)

    logger.info("[管理端] TTS 测试服务调用成功：url=%s bytes=%s file=%s", payload.url, len(pcm), filename)
    return {
        "ok": True,
        "bytes": len(pcm),
        "file": filename,
        "url": f"/api/files/{filename}",
        "sentences": sentences,
        "sent_to_device": payload.send_to_device,
        "sn": payload.sn,
    }


@router.post("/llm-tts-gateway")
async def llm_tts_gateway(payload: AdminLlmTtsGatewayRequest, _: None = Depends(require_admin)) -> dict[str, Any]:
    _ensure_voice_connected(payload.sn)
    prompt = payload.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="大模型输入不能为空")

    if payload.interrupt_current:
        await broadcaster.cancel_tts(payload.sn, "admin_llm_tts_gateway")

    message_id = f"admin-llm-tts-{int(time.time() * 1000)}"
    await broadcaster.publish(payload.sn, {
        "type": "agent_thought_start",
        "message_id": f"{message_id}-thought",
    }, persist=False)
    await broadcaster.publish(payload.sn, {
        "type": "agent_thought_delta",
        "message_id": f"{message_id}-thought",
        "content": "管理端输入已提交给大模型，准备生成可播报回复。",
    }, persist=False)
    await broadcaster.publish(payload.sn, {
        "type": "agent_thought_done",
        "message_id": f"{message_id}-thought",
    }, persist=False)

    try:
        reply = await llm_service.chat_text(prompt)
    except Exception as exc:
        logger.error("[管理端] 大模型调用失败：%s", exc)
        raise HTTPException(status_code=502, detail=f"大模型调用失败：{exc}") from exc

    await _publish_streaming_text(
        payload.sn,
        reply,
        message_id,
        payload.stream_chunk_chars,
        payload.stream_interval_ms / 1000,
    )

    sentences = tts_service.split_sentences(reply, payload.max_sentence_chars)
    total_bytes = 0
    files = []
    for index, sentence in enumerate(sentences, start=1):
        try:
            pcm = await tts_service.synthesize_pcm_via_gateway(
                text=sentence,
                url=payload.url,
                token=payload.token.strip(),
                response_format=payload.response_format,
                verify_ssl=payload.verify_ssl,
            )
        except Exception as exc:
            logger.error("[管理端] 分句 TTS 测试服务调用失败：sentence=%s error=%s", sentence, exc)
            await broadcaster.publish(payload.sn, {
                "type": "tts_failed",
                "message": f"第 {index} 段 TTS 测试服务调用失败：{exc}",
            }, persist=False)
            raise HTTPException(status_code=502, detail=f"第 {index} 段 TTS 测试服务调用失败：{exc}") from exc

        filename = f"admin-llm-tts-{int(time.time() * 1000)}-{index:02d}.pcm"
        target = settings.upload_dir / "tts" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(pcm)
        files.append(filename)
        total_bytes += len(pcm)
        await broadcaster.publish(payload.sn, {
            "type": "admin_llm_tts_sentence",
            "index": index,
            "total": len(sentences),
            "text": sentence,
            "bytes": len(pcm),
            "file": filename,
        }, persist=False)
        task = await broadcaster.send_voice(
            payload.sn,
            pcm,
            sentence,
            send_control_messages=payload.send_control_messages,
        )
        await task

    broadcaster.get(payload.sn).last_agent_reply = reply
    logger.info("[管理端] 大模型+测试TTS下发完成：sn=%s sentences=%s bytes=%s", payload.sn, len(sentences), total_bytes)
    return {
        "ok": True,
        "sn": payload.sn,
        "message_id": message_id,
        "text": reply,
        "sentences": sentences,
        "files": files,
        "bytes": total_bytes,
    }


@router.post("/send-cmd")
async def send_cmd(payload: AdminCmdRequest, _: None = Depends(require_admin)) -> dict[str, Any]:
    _ensure_cmd_connected(payload.sn)
    command = _build_device_command(payload.cmd, payload.msgid, payload.payload)
    ok = await broadcaster.send_cmd(payload.sn, command)
    if not ok:
        raise HTTPException(status_code=502, detail=f"设备 {payload.sn} cmd 命令下发失败")
    await broadcaster.publish(payload.sn, {
        "type": "admin_send_cmd",
        "command": command,
        "message": "管理端已下发设备命令。",
    }, persist=False)
    logger.info("[管理端] 已向设备 %s 下发命令：%s", payload.sn, command)
    return {"ok": True, "sn": payload.sn, "command": command}


@router.post("/broadcast-cmd")
async def broadcast_cmd(payload: AdminBroadcastCmdRequest, _: None = Depends(require_admin)) -> dict[str, Any]:
    command = _build_device_command(payload.cmd, payload.msgid, payload.payload)
    sent = []
    failed = []
    for sn, session in sorted(broadcaster.sessions.items()):
        if session.cmd_ws is None:
            continue
        if await broadcaster.send_cmd(sn, command):
            sent.append(sn)
            await broadcaster.publish(sn, {
                "type": "admin_broadcast_cmd",
                "command": command,
                "message": "管理端已广播设备命令。",
            }, persist=False)
        else:
            failed.append(sn)
    logger.info("[管理端] 广播命令：%s sent=%s failed=%s", command, sent, failed)
    return {"ok": True, "command": command, "sent": sent, "failed": failed}


@router.post("/cancel-tts")
async def cancel_tts(payload: AdminCancelRequest, _: None = Depends(require_admin)) -> dict[str, Any]:
    await broadcaster.cancel_tts(payload.sn, payload.reason or "admin_cancel")
    return {"ok": True, "sn": payload.sn}


async def _publish_streaming_text(sn: str, text: str, message_id: str, chunk_chars: int, interval_seconds: float) -> None:
    await broadcaster.publish(sn, {"type": "agent_start", "message_id": message_id})
    for index in range(0, len(text), chunk_chars):
        await broadcaster.publish(sn, {
            "type": "agent_delta",
            "message_id": message_id,
            "content": text[index:index + chunk_chars],
        })
        if interval_seconds:
            await asyncio.sleep(interval_seconds)
    await broadcaster.publish(sn, {"type": "agent_done", "message_id": message_id})


async def _restart_service_after_response(reason: str) -> None:
    global _service_restart_requested
    await asyncio.sleep(0.5)
    try:
        logger.warning("[管理端] 准备重启服务：pid=%s reason=%s", os.getpid(), reason)
        await broadcaster.notify_server_shutdown()
        argv = _restart_argv()
        logger.warning("[管理端] 使用命令重启服务：%s", argv)
        os.execv(sys.executable, argv)
    except Exception as exc:
        _service_restart_requested = False
        logger.error("[管理端] 服务自重启失败：%s", exc)


def _restart_argv() -> list[str]:
    original = getattr(sys, "orig_argv", None)
    if isinstance(original, list) and len(original) > 1:
        body = [str(arg) for arg in original[1:]]
        if not any(arg.lower().endswith((".exe", ".bat", ".cmd")) for arg in body):
            return [sys.executable, *body]

    return [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        settings.app_host,
        "--port",
        str(settings.app_port),
    ]


def _ensure_voice_connected(sn: str) -> None:
    session = broadcaster.sessions.get(sn)
    if session is None:
        raise HTTPException(status_code=404, detail=f"设备 {sn} 不存在或未连接")
    if session.voice_ws is None:
        raise HTTPException(status_code=409, detail=f"设备 {sn} 的 voice 通道未连接")


def _ensure_cmd_connected(sn: str) -> None:
    session = broadcaster.sessions.get(sn)
    if session is None:
        raise HTTPException(status_code=404, detail=f"设备 {sn} 不存在或未连接")
    if session.cmd_ws is None:
        raise HTTPException(status_code=409, detail=f"设备 {sn} 的 cmd 通道未连接")


def _build_device_command(cmd: str, msgid: int | None, payload: dict[str, Any]) -> dict[str, Any]:
    command = {"cmd": cmd, "msgid": msgid if msgid is not None else COMMAND_MSGIDS.get(cmd, int(time.time() * 1000))}
    command.update(payload or {})
    command["cmd"] = cmd
    return command


def _split_tts_sentences(text: str, max_chars: int) -> list[str]:
    parts = [item.strip() for item in re.split(r"(?<=[。！？!?；;])", text) if item.strip()]
    if not parts:
        parts = [text.strip()] if text.strip() else []

    result = []
    for part in parts:
        while len(part) > max_chars:
            split_at = max(part.rfind("，", 0, max_chars), part.rfind(",", 0, max_chars), part.rfind("、", 0, max_chars))
            if split_at <= 0:
                result.append(part[:max_chars].strip())
                part = part[max_chars:].strip()
                continue
            result.append(part[:split_at + 1].strip())
            part = part[split_at + 1:].strip()
        if part:
            result.append(part)
    return result
