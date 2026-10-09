from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from fastapi import WebSocket

from app.config import settings
from app.services.history import history_service
from app.services.logger import logger


@dataclass
class DeviceSession:
    sn: str
    cmd_ws: WebSocket | None = None
    voice_ws: WebSocket | None = None
    web_clients: set[WebSocket] = field(default_factory=set)
    latest_image: str | None = None
    session_id: str = ""
    processed_events: set[str] = field(default_factory=set)
    tts_generation: int = 0
    tts_task: asyncio.Task | None = None
    current_task: str | None = None
    task_state: str = "no_task"
    last_agent_reply: str = ""
    last_user_input: str = ""
    latest_hazard_result: dict[str, Any] = field(default_factory=dict)
    cmd_send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    voice_send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Broadcaster:
    def __init__(self) -> None:
        self.sessions: dict[str, DeviceSession] = {}
        self.buffers: dict[str, list[dict[str, Any]]] = {}

    def get(self, sn: str) -> DeviceSession:
        if sn not in self.sessions:
            self.sessions[sn] = DeviceSession(sn=sn, session_id=history_service.session_id(sn))
        return self.sessions[sn]

    async def remove_sessions_without_cmd(self) -> list[str]:
        removed: list[str] = []
        for sn, session in list(self.sessions.items()):
            if session.cmd_ws is not None:
                continue

            removed.append(sn)
            if session.tts_task and not session.tts_task.done():
                session.tts_task.cancel()

            if session.voice_ws is not None:
                try:
                    await session.voice_ws.close(code=1001)
                except Exception:
                    pass

            for ws in list(session.web_clients):
                try:
                    await ws.close(code=1001)
                except Exception:
                    pass

            self.sessions.pop(sn, None)
            self.buffers.pop(sn, None)

        return removed

    async def register_cmd(self, sn: str, ws: WebSocket) -> None:
        session = self.get(sn)
        session.cmd_ws = ws
        logger.info("[设备连接] 设备 %s 已连接 cmd 通道", sn)
        await self.publish(sn, {"type": "device_status", "cmd": "connected"})

    async def register_voice(self, sn: str, ws: WebSocket) -> None:
        session = self.get(sn)
        session.voice_ws = ws
        logger.info("[设备连接] 设备 %s 已连接 voice 通道", sn)
        await self.publish(sn, {"type": "device_status", "voice": "connected"})

    async def unregister_cmd(self, sn: str, ws: WebSocket) -> None:
        session = self.sessions.get(sn)
        if session is None:
            return
        if session.cmd_ws is ws:
            session.cmd_ws = None
        logger.info("[设备断开] 设备 %s cmd 通道已断开", sn)
        await self.publish(sn, {"type": "device_status", "cmd": "disconnected"}, persist=False)

    async def unregister_voice(self, sn: str, ws: WebSocket) -> None:
        session = self.sessions.get(sn)
        if session is None:
            return
        if session.voice_ws is ws:
            session.voice_ws = None
        logger.info("[设备断开] 设备 %s voice 通道已断开", sn)
        await self.publish(sn, {"type": "device_status", "voice": "disconnected"}, persist=False)

    async def register_web(self, sn: str, ws: WebSocket, replay: bool = True) -> None:
        session = self.get(sn)
        session.web_clients.add(ws)
        if replay:
            for event in self.buffers.get(sn, [])[-100:]:
                await ws.send_json(event)

    def unregister_web(self, sn: str, ws: WebSocket) -> None:
        session = self.sessions.get(sn)
        if session is None:
            return
        session.web_clients.discard(ws)

    async def publish(self, sn: str, event: dict[str, Any], persist: bool = True) -> None:
        session = self.get(sn)
        full_event = {"version": "1.0", "sn": sn, **event}
        self.buffers.setdefault(sn, []).append(full_event)
        self.buffers[sn] = self.buffers[sn][-100:]
        if persist:
            history_service.append(sn, {"session_id": session.session_id, **full_event})

        dead = []
        for ws in list(session.web_clients):
            try:
                await asyncio.wait_for(ws.send_json(full_event), timeout=5)
            except Exception:
                dead.append(ws)
        for ws in dead:
            session.web_clients.discard(ws)

    async def send_cmd(self, sn: str, payload: dict[str, Any]) -> bool:
        session = self.get(sn)
        ws = session.cmd_ws
        if ws is None:
            logger.warning("[命令下发] 设备 %s cmd 通道未连接", sn)
            return False

        text = json.dumps(payload, ensure_ascii=False)
        try:
            async with session.cmd_send_lock:
                await asyncio.wait_for(ws.send_text(text), timeout=5)
            logger.info("[命令下发] 设备 %s cmd=%s msgid=%s 发送完成", sn, payload.get("cmd"), payload.get("msgid"))
            return True
        except Exception as exc:
            logger.error("[命令下发] 设备 %s cmd 消息发送失败：%s", sn, exc)
            return False

    async def disconnect_client(self, sn: str, reason: str = "admin_disconnect") -> dict[str, Any] | None:
        session = self.sessions.get(sn)
        if session is None:
            return None

        cmd_connected = session.cmd_ws is not None
        voice_connected = session.voice_ws is not None
        web_clients = len(session.web_clients)
        command_sent = False

        if session.tts_task and not session.tts_task.done():
            session.tts_task.cancel()
        session.tts_generation += 1

        if session.cmd_ws is not None:
            command_sent = await self.send_cmd(sn, {"msgid": 9001, "cmd": "disconnect", "reason": reason})

        await self.publish(sn, {
            "type": "device_status",
            "cmd": "disconnect" if cmd_connected else "disconnected",
            "voice": "disconnect" if voice_connected else "disconnected",
            "reason": reason,
        }, persist=False)

        for ws in [session.cmd_ws, session.voice_ws, *list(session.web_clients)]:
            if ws is None:
                continue
            try:
                await ws.close(code=1001)
            except Exception:
                pass

        self.sessions.pop(sn, None)
        self.buffers.pop(sn, None)
        logger.info(
            "[管理端] 已主动断开设备 %s：cmd=%s voice=%s web_clients=%s command_sent=%s",
            sn,
            cmd_connected,
            voice_connected,
            web_clients,
            command_sent,
        )
        return {
            "sn": sn,
            "cmd_connected": cmd_connected,
            "voice_connected": voice_connected,
            "web_clients": web_clients,
            "command_sent": command_sent,
        }

    async def notify_server_shutdown(self) -> None:
        tasks = []
        payload = {"msgid": 9000, "cmd": "disconnect", "reason": "server_shutdown"}
        for sn, session in list(self.sessions.items()):
            if session.cmd_ws is None:
                continue
            tasks.append(self.send_cmd(sn, payload))
            await self.publish(sn, {
                "type": "device_status",
                "cmd": "disconnect",
                "reason": "server_shutdown",
            }, persist=False)

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            ok_count = sum(1 for result in results if result is True)
            logger.info("[服务关闭] 已向 %s/%s 个 cmd 通道广播 disconnect", ok_count, len(tasks))

        for session in list(self.sessions.values()):
            if session.tts_task and not session.tts_task.done():
                session.tts_task.cancel()
            for ws in [session.cmd_ws, session.voice_ws, *list(session.web_clients)]:
                if ws is None:
                    continue
                try:
                    await ws.close(code=1001)
                except Exception:
                    pass

    def _track_background_task(self, task: asyncio.Task, label: str) -> None:
        def _done(done: asyncio.Task) -> None:
            try:
                done.result()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.error("[后台任务] %s 执行失败：%s", label, exc)

        task.add_done_callback(_done)

    async def cancel_tts(self, sn: str, reason: str = "new_pcm_received") -> None:
        session = self.get(sn)
        session.tts_generation += 1
        task = asyncio.create_task(self.send_cmd(sn, {"msgid": 2004, "cmd": "voice_cancel", "reason": reason}))
        self._track_background_task(task, "下发语音中断命令")
        await self.publish(sn, {"type": "tts_cancelled", "reason": reason}, persist=False)
        source = "新文字输入" if reason == "new_text_received" else "新 PCM 到达"
        logger.info("[语音下发] %s，已中断设备 %s 上一段 TTS", source, sn)

    async def send_voice(self, sn: str, pcm: bytes, text: str, send_control_messages: bool = True) -> asyncio.Task:
        async def _single_part() -> AsyncIterator[tuple[str, bytes]]:
            yield text, pcm

        return await self.send_voice_stream(sn, _single_part(), text, send_control_messages=send_control_messages)

    async def send_voice_stream(
        self,
        sn: str,
        parts: AsyncIterator[tuple[str, bytes]],
        text: str,
        send_control_messages: bool = True,
    ) -> asyncio.Task:
        session = self.get(sn)
        generation = session.tts_generation + 1
        session.tts_generation = generation

        async def _run() -> None:
            if session.voice_ws is None:
                await self.publish(sn, {"type": "tts_failed", "message": "voice 通道未连接"})
                logger.warning("[异常] voice 通道未连接，TTS 结果仅推送 Web 端")
                return

            try:
                started = False
                total_bytes = 0
                async for part_text, pcm in parts:
                    if session.tts_generation != generation:
                        logger.info("[语音下发] 设备 %s TTS 下发已被新一轮语音打断", sn)
                        return
                    if not started:
                        if send_control_messages:
                            await self.send_cmd(sn, {
                                "msgid": 2002,
                                "cmd": "voice_start",
                                "format": "pcm_s16le",
                                "sample_rate": settings.pcm_sample_rate,
                                "channels": settings.pcm_channels,
                            })
                        await self.publish(sn, {"type": "tts_start", "text": text})
                        started = True
                    await self.publish(sn, {
                        "type": "tts_segment_start",
                        "text": part_text,
                        "bytes": len(pcm),
                    }, persist=False)
                    for offset in range(0, len(pcm), settings.tts_chunk_size):
                        if session.tts_generation != generation:
                            logger.info("[语音下发] 设备 %s TTS 下发已被新一轮语音打断", sn)
                            return
                        chunk = pcm[offset: offset + settings.tts_chunk_size]
                        async with session.voice_send_lock:
                            await asyncio.wait_for(session.voice_ws.send_bytes(chunk), timeout=5)
                        await asyncio.sleep(0.02)
                    total_bytes += len(pcm)

                if started and send_control_messages:
                    await self.send_cmd(sn, {"msgid": 2003, "cmd": "voice_end"})
                await self.publish(sn, {"type": "tts_done", "bytes": total_bytes})
                logger.info("[语音下发] 设备 %s TTS 下发完成，字节数 %s", sn, total_bytes)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                await self.publish(sn, {"type": "tts_failed", "message": "TTS 下发失败"})
                logger.error("[语音下发] 设备 %s TTS 下发失败：%s", sn, exc)

        session.tts_task = asyncio.create_task(_run())
        self._track_background_task(session.tts_task, "下发 TTS 语音")
        return session.tts_task


broadcaster = Broadcaster()
