from __future__ import annotations

import argparse
import asyncio
import math
import signal
import struct
import time
from pathlib import Path
from typing import Any, Iterable

import websockets


ClientSet = set[Any]


class PcmBroadcastServer:
    def __init__(
        self,
        pcm_path: Path | None,
        chunk_size: int,
        interval_ms: int,
        repeat: bool,
        sample_rate: int,
        channels: int,
        tone_hz: float,
        tone_seconds: float,
        send_events: bool,
    ) -> None:
        self.pcm_path = pcm_path
        self.chunk_size = max(chunk_size, 1)
        self.interval_seconds = max(interval_ms, 1) / 1000
        self.repeat = repeat
        self.sample_rate = sample_rate
        self.channels = channels
        self.tone_hz = tone_hz
        self.tone_seconds = tone_seconds
        self.send_events = send_events
        self.clients: ClientSet = set()
        self.stopped = asyncio.Event()
        self.round_index = 0

    async def handle_client(self, websocket: Any, *_: object) -> None:
        self.clients.add(websocket)
        peer = getattr(websocket, "remote_address", "unknown")
        print(f"[client] connected: {peer}; clients={len(self.clients)}")
        try:
            async for message in websocket:
                if isinstance(message, str) and message.strip().lower() in {"ping", "status"}:
                    await websocket.send(f"clients={len(self.clients)}")
        except websockets.ConnectionClosed:
            pass
        finally:
            self.clients.discard(websocket)
            print(f"[client] disconnected: {peer}; clients={len(self.clients)}")

    async def run_broadcast_loop(self) -> None:
        while not self.stopped.is_set():
            self.round_index += 1
            source = str(self.pcm_path) if self.pcm_path else f"tone:{self.tone_hz}Hz"
            print(f"[broadcast] round={self.round_index} source={source}")
            await self.broadcast_event("pcm_start")
            sent = 0
            started = time.perf_counter()
            for chunk in self.iter_pcm_chunks():
                if self.stopped.is_set():
                    break
                await self.broadcast_bytes(chunk)
                sent += len(chunk)
                await asyncio.sleep(self.interval_seconds)
            elapsed = max(time.perf_counter() - started, 0.001)
            await self.broadcast_event("pcm_end")
            print(f"[broadcast] done bytes={sent} elapsed={elapsed:.2f}s speed={sent / elapsed:.0f}B/s")
            if not self.repeat:
                break
            await asyncio.sleep(0.5)
        self.stopped.set()

    def iter_pcm_chunks(self) -> Iterable[bytes]:
        if self.pcm_path:
            with self.pcm_path.open("rb") as file:
                while True:
                    chunk = file.read(self.chunk_size)
                    if not chunk:
                        break
                    yield chunk
            return

        total_samples = int(self.sample_rate * self.tone_seconds)
        frame = bytearray()
        for index in range(total_samples):
            value = int(3000 * math.sin(2 * math.pi * self.tone_hz * index / self.sample_rate))
            for _ in range(self.channels):
                frame.extend(struct.pack("<h", value))
            if len(frame) >= self.chunk_size:
                yield bytes(frame)
                frame.clear()
        if frame:
            yield bytes(frame)

    async def broadcast_event(self, event_type: str) -> None:
        if not self.send_events:
            return
        payload = (
            f'{{"type":"{event_type}","sample_rate":{self.sample_rate},'
            f'"channels":{self.channels},"sample_width":16,"round":{self.round_index}}}'
        )
        await self.broadcast_text(payload)

    async def broadcast_text(self, text: str) -> None:
        if not self.clients:
            return
        await self._send_to_all(text)

    async def broadcast_bytes(self, data: bytes) -> None:
        if not self.clients:
            return
        await self._send_to_all(data)

    async def _send_to_all(self, payload: str | bytes) -> None:
        stale = []
        for client in list(self.clients):
            try:
                await client.send(payload)
            except websockets.ConnectionClosed:
                stale.append(client)
        for client in stale:
            self.clients.discard(client)


async def main() -> None:
    parser = argparse.ArgumentParser(description="PCM WebSocket 广播测试服务器")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=18888)
    parser.add_argument("--pcm", default="", help="要广播的 PCM 文件；不填则生成测试音")
    parser.add_argument("--chunk-size", type=int, default=3200, help="每次广播的字节数，默认 100ms 16k/16bit/mono")
    parser.add_argument("--interval-ms", type=int, default=100, help="每个 chunk 的发送间隔")
    parser.add_argument("--once", action="store_true", help="只广播一轮后退出；默认循环广播")
    parser.add_argument("--sample-rate", type=int, default=16000)
    parser.add_argument("--channels", type=int, default=1)
    parser.add_argument("--tone-hz", type=float, default=440.0)
    parser.add_argument("--tone-seconds", type=float, default=3.0)
    parser.add_argument("--send-events", action="store_true", help="广播前后发送 pcm_start/pcm_end JSON 文本事件")
    args = parser.parse_args()

    pcm_path = Path(args.pcm) if args.pcm else None
    if pcm_path and not pcm_path.exists():
        raise FileNotFoundError(f"PCM 文件不存在：{pcm_path}")

    server = PcmBroadcastServer(
        pcm_path=pcm_path,
        chunk_size=args.chunk_size,
        interval_ms=args.interval_ms,
        repeat=not args.once,
        sample_rate=args.sample_rate,
        channels=args.channels,
        tone_hz=args.tone_hz,
        tone_seconds=args.tone_seconds,
        send_events=args.send_events,
    )

    loop = asyncio.get_running_loop()
    for signame in ("SIGINT", "SIGTERM"):
        if hasattr(signal, signame):
            try:
                loop.add_signal_handler(getattr(signal, signame), server.stopped.set)
            except NotImplementedError:
                pass

    async with websockets.serve(server.handle_client, args.host, args.port):
        print(f"[server] PCM broadcast server listening: ws://{args.host}:{args.port}")
        print("[server] clients receive binary PCM frames; Ctrl+C to stop")
        producer = asyncio.create_task(server.run_broadcast_loop())
        await server.stopped.wait()
        producer.cancel()
        try:
            await producer
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    asyncio.run(main())
