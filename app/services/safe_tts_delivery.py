from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from app.config import settings
from app.services.broadcaster import broadcaster
from app.services.logger import logger
from app.services.tts import tts_service


def _env_float(name: str, default: float) -> float:
    raw = str((__import__("os").getenv(name, str(default)) or default)).strip()
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env_float(name, float(default)))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = str((__import__("os").getenv(name, "1" if default else "0") or "")).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


@dataclass
class SafeTtsDeliveryResult:
    sn: str
    ok: bool
    generation: int
    bytes_sent: int = 0
    cancelled: bool = False
    reason: str = ""
    detail: str = ""
    elapsed_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "sn": self.sn,
            "ok": self.ok,
            "generation": self.generation,
            "bytes_sent": self.bytes_sent,
            "cancelled": self.cancelled,
            "reason": self.reason,
            "detail": self.detail,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass
class _DeviceGate:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    active_task: asyncio.Task | None = None
    active_generation: int = 0


class SafeTtsDeliveryService:
    """独立的安全 TTS 下发服务。

    不修改原 broadcaster.send_voice / cancel_tts 实现，另起一条更稳的下发链路：
    1) 硬中断旧任务（cancel + await 退出）
    2) 等待 voice_cancel 真正发出
    3) 设备 settle + 可选静音冲刷
    4) 再 voice_start / PCM / voice_end
    5) 同 sn 串行，避免并发叠音
    """

    def __init__(self) -> None:
        self._gates: dict[str, _DeviceGate] = {}
        self.settle_ms = max(0, _env_int("SAFE_TTS_SETTLE_MS", 120))
        self.flush_silence_ms = max(0, _env_int("SAFE_TTS_FLUSH_SILENCE_MS", 80))
        self.require_cmd = _env_bool("SAFE_TTS_REQUIRE_CMD", True)
        self.chunk_size = max(320, _env_int("SAFE_TTS_CHUNK_SIZE", settings.tts_chunk_size))
        self.chunk_delay_ms = max(0, _env_int("SAFE_TTS_CHUNK_DELAY_MS", 20))
        self.cancel_wait_timeout_s = max(0.2, _env_float("SAFE_TTS_CANCEL_WAIT_TIMEOUT_S", 2.0))

    def _gate(self, sn: str) -> _DeviceGate:
        if sn not in self._gates:
            self._gates[sn] = _DeviceGate()
        return self._gates[sn]

    def _silence_pcm(self, duration_ms: int) -> bytes:
        if duration_ms <= 0:
            return b""
        frame_bytes = max(1, settings.pcm_channels) * max(1, settings.pcm_sample_width // 8)
        samples = int(settings.pcm_sample_rate * duration_ms / 1000)
        return b"\x00" * (samples * frame_bytes)

    async def status(self, sn: str) -> dict[str, Any]:
        session = broadcaster.get(sn)
        gate = self._gate(sn)
        return {
            "sn": sn,
            "cmd_connected": session.cmd_ws is not None,
            "voice_connected": session.voice_ws is not None,
            "legacy_tts_generation": session.tts_generation,
            "legacy_tts_running": bool(session.tts_task and not session.tts_task.done()),
            "safe_tts_running": bool(gate.active_task and not gate.active_task.done()),
            "safe_tts_generation": gate.active_generation,
            "settle_ms": self.settle_ms,
            "flush_silence_ms": self.flush_silence_ms,
            "require_cmd": self.require_cmd,
            "chunk_size": self.chunk_size,
            "chunk_delay_ms": self.chunk_delay_ms,
        }

    async def cancel(
        self,
        sn: str,
        reason: str = "safe_tts_cancel",
        *,
        wait_legacy: bool = True,
        send_control: bool = True,
        flush_silence: bool = False,
    ) -> SafeTtsDeliveryResult:
        started = time.perf_counter()
        gate = self._gate(sn)
        async with gate.lock:
            result = await self._cancel_locked(
                sn,
                reason=reason,
                wait_legacy=wait_legacy,
                send_control=send_control,
                flush_silence=flush_silence,
            )
            result.elapsed_ms = int((time.perf_counter() - started) * 1000)
            return result

    async def send_pcm(
        self,
        sn: str,
        pcm: bytes,
        text: str = "",
        *,
        reason: str = "safe_tts_send",
        interrupt_current: bool = True,
        send_control_messages: bool = True,
        flush_before_start: bool | None = None,
    ) -> SafeTtsDeliveryResult:
        async def _parts() -> AsyncIterator[tuple[str, bytes]]:
            yield text or "safe-tts-pcm", pcm

        return await self.send_stream(
            sn,
            _parts(),
            text=text or "safe-tts-pcm",
            reason=reason,
            interrupt_current=interrupt_current,
            send_control_messages=send_control_messages,
            flush_before_start=flush_before_start,
        )

    async def send_text(
        self,
        sn: str,
        text: str,
        *,
        reason: str = "safe_tts_text",
        interrupt_current: bool = True,
        send_control_messages: bool = True,
        flush_before_start: bool | None = None,
    ) -> SafeTtsDeliveryResult:
        parts = await tts_service.synthesize_pcm_parts(text)

        async def _parts() -> AsyncIterator[tuple[str, bytes]]:
            for part_text, pcm in parts:
                yield part_text, pcm

        return await self.send_stream(
            sn,
            _parts(),
            text=text,
            reason=reason,
            interrupt_current=interrupt_current,
            send_control_messages=send_control_messages,
            flush_before_start=flush_before_start,
        )

    async def send_stream(
        self,
        sn: str,
        parts: AsyncIterator[tuple[str, bytes]],
        text: str,
        *,
        reason: str = "safe_tts_stream",
        interrupt_current: bool = True,
        send_control_messages: bool = True,
        flush_before_start: bool | None = None,
    ) -> SafeTtsDeliveryResult:
        started = time.perf_counter()
        gate = self._gate(sn)
        async with gate.lock:
            session = broadcaster.get(sn)
            if session.voice_ws is None:
                result = SafeTtsDeliveryResult(
                    sn=sn,
                    ok=False,
                    generation=session.tts_generation,
                    reason=reason,
                    detail="voice 通道未连接",
                )
                await broadcaster.publish(sn, {"type": "safe_tts_failed", "message": result.detail, "reason": reason})
                result.elapsed_ms = int((time.perf_counter() - started) * 1000)
                return result

            if self.require_cmd and session.cmd_ws is None and send_control_messages:
                result = SafeTtsDeliveryResult(
                    sn=sn,
                    ok=False,
                    generation=session.tts_generation,
                    reason=reason,
                    detail="cmd 通道未连接，安全 TTS 拒绝下发（避免无 start/cancel 控制命令导致杂音）",
                )
                await broadcaster.publish(sn, {"type": "safe_tts_failed", "message": result.detail, "reason": reason})
                logger.warning("[安全TTS] 设备 %s cmd 未连接，拒绝下发", sn)
                result.elapsed_ms = int((time.perf_counter() - started) * 1000)
                return result

            if interrupt_current:
                await self._cancel_locked(
                    sn,
                    reason=f"{reason}_preempt",
                    wait_legacy=True,
                    send_control=send_control_messages,
                    flush_silence=False,
                )

            do_flush = self.flush_silence_ms > 0 if flush_before_start is None else flush_before_start
            generation = session.tts_generation + 1
            session.tts_generation = generation
            gate.active_generation = generation

            task = asyncio.create_task(
                self._deliver(
                    sn=sn,
                    generation=generation,
                    parts=parts,
                    text=text,
                    reason=reason,
                    send_control_messages=send_control_messages,
                    flush_before_start=do_flush,
                )
            )
            gate.active_task = task
            # 同步挂到 session.tts_task，便于原管理端/状态看到“有 TTS 在跑”
            session.tts_task = task
            try:
                result = await task
            finally:
                if gate.active_task is task:
                    gate.active_task = None
                if session.tts_task is task:
                    session.tts_task = None
            result.elapsed_ms = int((time.perf_counter() - started) * 1000)
            return result

    async def _cancel_locked(
        self,
        sn: str,
        *,
        reason: str,
        wait_legacy: bool,
        send_control: bool,
        flush_silence: bool,
    ) -> SafeTtsDeliveryResult:
        session = broadcaster.get(sn)
        gate = self._gate(sn)
        session.tts_generation += 1
        generation = session.tts_generation
        gate.active_generation = generation

        stopped = await self._stop_task(gate.active_task, label=f"safe-tts:{sn}")
        if wait_legacy:
            stopped = (await self._stop_task(session.tts_task, label=f"legacy-tts:{sn}")) or stopped

        cancel_sent = False
        if send_control:
            cancel_sent = await broadcaster.send_cmd(
                sn,
                {
                    "version": "1.0",
                    "msgid": 2004,
                    "cmd": "voice_cancel",
                    "reason": reason,
                },
            )

        if self.settle_ms > 0:
            await asyncio.sleep(self.settle_ms / 1000)

        flushed = 0
        if flush_silence and session.voice_ws is not None:
            silence = self._silence_pcm(self.flush_silence_ms)
            if silence:
                try:
                    async with session.voice_send_lock:
                        await asyncio.wait_for(session.voice_ws.send_bytes(silence), timeout=5)
                    flushed = len(silence)
                except Exception as exc:
                    logger.warning("[安全TTS] 设备 %s 静音冲刷失败：%s", sn, exc)

        await broadcaster.publish(
            sn,
            {
                "type": "safe_tts_cancelled",
                "reason": reason,
                "cancel_sent": cancel_sent,
                "stopped_task": stopped,
                "flushed_bytes": flushed,
            },
            persist=False,
        )
        logger.info(
            "[安全TTS] 已中断设备 %s reason=%s cancel_sent=%s stopped=%s flushed=%s gen=%s",
            sn,
            reason,
            cancel_sent,
            stopped,
            flushed,
            generation,
        )
        return SafeTtsDeliveryResult(
            sn=sn,
            ok=True,
            generation=generation,
            bytes_sent=flushed,
            cancelled=True,
            reason=reason,
            detail="cancelled",
        )

    async def _stop_task(self, task: asyncio.Task | None, label: str) -> bool:
        if task is None or task.done():
            return False
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=self.cancel_wait_timeout_s)
        except asyncio.TimeoutError:
            logger.warning("[安全TTS] 等待任务退出超时：%s", label)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("[安全TTS] 等待任务退出异常：%s err=%s", label, exc)
        return True

    async def _deliver(
        self,
        *,
        sn: str,
        generation: int,
        parts: AsyncIterator[tuple[str, bytes]],
        text: str,
        reason: str,
        send_control_messages: bool,
        flush_before_start: bool,
    ) -> SafeTtsDeliveryResult:
        session = broadcaster.get(sn)
        if session.voice_ws is None:
            return SafeTtsDeliveryResult(
                sn=sn,
                ok=False,
                generation=generation,
                reason=reason,
                detail="voice 通道未连接",
            )

        started = False
        total_bytes = 0
        try:
            if flush_before_start:
                silence = self._silence_pcm(self.flush_silence_ms)
                if silence:
                    # 先冲刷一小段静音，帮助设备清播放残留；仍受 generation 保护
                    if session.tts_generation != generation:
                        return SafeTtsDeliveryResult(
                            sn=sn,
                            ok=False,
                            generation=generation,
                            cancelled=True,
                            reason=reason,
                            detail="preempted_before_flush",
                        )
                    if send_control_messages and not started:
                        ok = await broadcaster.send_cmd(
                            sn,
                            {
                                "version": "1.0",
                                "msgid": 2002,
                                "cmd": "voice_start",
                                "format": "pcm_s16le",
                                "sample_rate": settings.pcm_sample_rate,
                                "channels": settings.pcm_channels,
                            },
                        )
                        if not ok and self.require_cmd:
                            return SafeTtsDeliveryResult(
                                sn=sn,
                                ok=False,
                                generation=generation,
                                reason=reason,
                                detail="voice_start 发送失败",
                            )
                        started = True
                        await broadcaster.publish(sn, {"type": "safe_tts_start", "text": text, "reason": reason})
                    async with session.voice_send_lock:
                        await asyncio.wait_for(session.voice_ws.send_bytes(silence), timeout=5)
                    total_bytes += len(silence)
                    await asyncio.sleep(self.chunk_delay_ms / 1000)

            async for part_text, pcm in parts:
                if session.tts_generation != generation:
                    logger.info("[安全TTS] 设备 %s 下发被新一轮打断 gen=%s", sn, generation)
                    return SafeTtsDeliveryResult(
                        sn=sn,
                        ok=False,
                        generation=generation,
                        bytes_sent=total_bytes,
                        cancelled=True,
                        reason=reason,
                        detail="preempted",
                    )
                if not pcm:
                    continue
                if not started:
                    if send_control_messages:
                        ok = await broadcaster.send_cmd(
                            sn,
                            {
                                "version": "1.0",
                                "msgid": 2002,
                                "cmd": "voice_start",
                                "format": "pcm_s16le",
                                "sample_rate": settings.pcm_sample_rate,
                                "channels": settings.pcm_channels,
                            },
                        )
                        if not ok and self.require_cmd:
                            return SafeTtsDeliveryResult(
                                sn=sn,
                                ok=False,
                                generation=generation,
                                reason=reason,
                                detail="voice_start 发送失败",
                            )
                    await broadcaster.publish(sn, {"type": "safe_tts_start", "text": text, "reason": reason})
                    started = True

                await broadcaster.publish(
                    sn,
                    {
                        "type": "safe_tts_segment_start",
                        "text": part_text,
                        "bytes": len(pcm),
                    },
                    persist=False,
                )

                for offset in range(0, len(pcm), self.chunk_size):
                    if session.tts_generation != generation:
                        logger.info("[安全TTS] 设备 %s chunk 下发被打断 gen=%s", sn, generation)
                        return SafeTtsDeliveryResult(
                            sn=sn,
                            ok=False,
                            generation=generation,
                            bytes_sent=total_bytes,
                            cancelled=True,
                            reason=reason,
                            detail="preempted_mid_chunk_loop",
                        )
                    chunk = pcm[offset : offset + self.chunk_size]
                    async with session.voice_send_lock:
                        await asyncio.wait_for(session.voice_ws.send_bytes(chunk), timeout=5)
                    total_bytes += len(chunk)
                    if self.chunk_delay_ms > 0:
                        await asyncio.sleep(self.chunk_delay_ms / 1000)

            if started and send_control_messages and session.tts_generation == generation:
                await broadcaster.send_cmd(
                    sn,
                    {
                        "version": "1.0",
                        "msgid": 2003,
                        "cmd": "voice_end",
                    },
                )
            if session.tts_generation != generation:
                return SafeTtsDeliveryResult(
                    sn=sn,
                    ok=False,
                    generation=generation,
                    bytes_sent=total_bytes,
                    cancelled=True,
                    reason=reason,
                    detail="preempted_before_end",
                )

            await broadcaster.publish(
                sn,
                {
                    "type": "safe_tts_done",
                    "bytes": total_bytes,
                    "reason": reason,
                },
            )
            logger.info("[安全TTS] 设备 %s 下发完成 bytes=%s gen=%s", sn, total_bytes, generation)
            return SafeTtsDeliveryResult(
                sn=sn,
                ok=True,
                generation=generation,
                bytes_sent=total_bytes,
                reason=reason,
                detail="done",
            )
        except asyncio.CancelledError:
            logger.info("[安全TTS] 设备 %s 下发任务被取消 gen=%s", sn, generation)
            return SafeTtsDeliveryResult(
                sn=sn,
                ok=False,
                generation=generation,
                bytes_sent=total_bytes,
                cancelled=True,
                reason=reason,
                detail="task_cancelled",
            )
        except Exception as exc:
            logger.error("[安全TTS] 设备 %s 下发失败：%s", sn, exc)
            await broadcaster.publish(
                sn,
                {
                    "type": "safe_tts_failed",
                    "message": str(exc),
                    "reason": reason,
                },
            )
            return SafeTtsDeliveryResult(
                sn=sn,
                ok=False,
                generation=generation,
                bytes_sent=total_bytes,
                reason=reason,
                detail=f"error:{exc}",
            )


safe_tts_delivery_service = SafeTtsDeliveryService()
