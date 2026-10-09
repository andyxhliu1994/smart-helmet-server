from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
import os
import shutil
import subprocess
import struct
import time
import wave
from pathlib import Path
from urllib.parse import quote, urlencode
from typing import Any, Awaitable, Callable

import httpx
import websockets

try:
    import msvcrt
except ImportError:  # pragma: no cover
    msvcrt = None

try:
    import winsound
except ImportError:  # pragma: no cover
    winsound = None


JPEG_1X1 = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAH/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAEFAqf/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAEDAQE/ASP/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAECAQE/ASP/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAY/Aqf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAE/IV//2gAMAwEAAgADAAAAEP/EFBQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQMBAT8QH//EFBQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQIBAT8QH//EFBABAQAAAAAAAAAAAAAAAAAAABD/2gAIAQEAAT8QH//Z"
)
DEFAULT_PANEL_IMAGE_URL = os.getenv("DEMO_PANEL_IMAGE_URL", "")
RECTIFICATION_IMAGE_URL = os.getenv("DEMO_RECTIFICATION_IMAGE_URL", "")
FOLLOW_UP_PROMPT_TEXT = "如果有细节或其他作业内容需要检查分析，请您随时告诉我"

DEFAULT_FLOW_OUTPUTS: dict[str, dict[str, Any]] = {
    "helmet_start": {
        "step_title": "场景1：班组长戴上智能头盔",
        "step": 1,
        "scene": "场景1",
        "title": "班组长戴上智能头盔",
        "text": "展示开始：深圳歌剧院工地，班组长佩戴智能头盔。",
    },
    "ask_flow": {
        "step_title": "场景2：询问检查流程",
        "asset": "step_ask_flow.pcm",
        "cmd": "upload_pcm",
        "msgid": 126,
    },
    "agent_flow": {
        "step_title": "Agent 播报检查流程",
        "step": 3,
        "scene": "场景2",
        "title": "小班副播报检查流程",
        "text": "Web端展示7项检查内容，设备端接收TTS。",
    },
    "photo_prompt": {
        "step_title": "提示拍照上传",
        "step": 4,
        "scene": "场景2",
        "title": "提示拍照上传",
        "text": "请前往需检查的三级配电箱线路现场，并拍摄图像上传",
    },
    "upload_panel_image": {
        "step_title": "场景3：上传配电箱图片",
        "asset": "step_upload_panel.jpeg",
        "cmd": "upload_jpeg",
        "msgid": 127,
    },
    "analysis": {
        "step_title": "分析现场隐患",
        "step": 6,
        "scene": "场景3",
        "title": "分析现场隐患",
        "text": "Agent正在结合现场图像分析是否存在隐患。",
    },
    "rectification_advice": {
        "step_title": "播报整改建议",
        "step": 7,
        "scene": "场景3",
        "title": "播报整改建议",
        "text": "Web端显示隐患内容和整改建议，设备端接收TTS。",
    },
    "confirm_rectification": {
        "step_title": "工人确认整改",
        "asset": "step_confirm.pcm",
        "cmd": "upload_pcm",
        "msgid": 128,
    },
    "rectification_image": {
        "step_title": "显示整改后图片",
        "step": 8,
        "scene": "场景3",
        "title": "整改后现场图片",
        "text": "收到，我们会立即整改，谢谢！整改后现场图片已显示。",
        "image_url": RECTIFICATION_IMAGE_URL,
        "image_role": "device",
    },
    "repeat": {
        "step_title": "智能分支：重复上一条回答",
        "asset": "step_repeat.pcm",
        "cmd": "upload_pcm",
        "msgid": 129,
    },
    "empty_pcm": {
        "step_title": "异常分支：空 PCM",
        "asset": "empty.pcm",
        "cmd": "upload_pcm",
        "msgid": 130,
    },
    "non_site_image": {
        "step_title": "异常分支：非施工场景图片",
        "asset": "non_site.jpeg",
        "cmd": "upload_jpeg",
        "msgid": 131,
    },
    "blurry_image": {
        "step_title": "异常分支：模糊图片",
        "asset": "blurry_panel.jpeg",
        "cmd": "upload_jpeg",
        "msgid": 132,
    },
    "demo_end": {
        "step_title": "演示结束",
    },
}


StepAction = Callable[[], Awaitable[None]]


def video_encoder_args(ffmpeg: str) -> list[str]:
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            check=True,
            capture_output=True,
            text=True,
        )
        encoders = f"{result.stdout}\n{result.stderr}"
    except Exception:
        encoders = ""
    if "libx264" in encoders:
        return ["-c:v", "libx264"]
    if "libopenh264" in encoders:
        return ["-c:v", "libopenh264"]
    return ["-c:v", "mpeg4"]


class ChromeBrowserRecorder:
    def __init__(
        self,
        chrome_path: str,
        url: str,
        output_dir: Path,
        ffmpeg_path: str = "",
        mp4_path: str = "",
        fps: float = 8.0,
        headed: bool = False,
        port: int = 9400,
    ) -> None:
        self.chrome_path = chrome_path
        self.url = url
        self.output_dir = output_dir
        self.frame_dir = output_dir / "browser_frames"
        self.ffmpeg_path = ffmpeg_path
        self.mp4_path = Path(mp4_path) if mp4_path else output_dir / "browser_recording.mp4"
        self.fps = max(fps, 1.0)
        self.headed = headed
        self.port = port
        self.proc: subprocess.Popen | None = None
        self.ws = None
        self.reader_task: asyncio.Task | None = None
        self.msgid = 0
        self.pending: dict[int, asyncio.Future] = {}
        self.frame_index = 0

    async def start(self) -> None:
        if not Path(self.chrome_path).exists():
            print(f"[record] Chrome not found: {self.chrome_path}")
            return
        self.frame_dir.mkdir(parents=True, exist_ok=True)
        profile_dir = (self.output_dir / "chrome_record_profile").resolve()
        profile_dir.mkdir(parents=True, exist_ok=True)
        args = [
            self.chrome_path,
            "--disable-gpu",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-sync",
            "--no-proxy-server",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-size=390,844",
            f"--user-data-dir={profile_dir}",
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={self.port}",
            "about:blank",
        ]
        if not self.headed:
            args.insert(1, "--headless=new")
        self.proc = await asyncio.to_thread(subprocess.Popen, args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        tab = await self._open_tab()
        ws_url = tab.get("webSocketDebuggerUrl")
        if not ws_url:
            raise RuntimeError("Chrome did not return webSocketDebuggerUrl")
        self.ws = await websockets.connect(ws_url, max_size=8 * 1024 * 1024)
        self.reader_task = asyncio.create_task(self._read_loop())
        await self.call("Page.enable")
        await self.call("Emulation.setDeviceMetricsOverride", {
            "width": 390,
            "height": 844,
            "deviceScaleFactor": 2,
            "mobile": True,
        })
        await self.call("Page.navigate", {"url": self.url})
        await asyncio.sleep(1.5)
        await self.call("Page.startScreencast", {
            "format": "jpeg",
            "quality": 82,
            "maxWidth": 780,
            "maxHeight": 1688,
            "everyNthFrame": 1,
        })
        print(f"[record] Chrome browser recording started: {self.url}")

    async def stop(self) -> None:
        try:
            if self.ws:
                try:
                    await self.call("Page.stopScreencast")
                except Exception:
                    pass
                await self.ws.close()
            if self.reader_task:
                self.reader_task.cancel()
        finally:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
                try:
                    await asyncio.to_thread(self.proc.wait, timeout=5)
                except Exception:
                    self.proc.kill()
        self.export_mp4()

    async def _open_tab(self) -> dict:
        endpoint = f"http://127.0.0.1:{self.port}"
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            for _ in range(80):
                try:
                    resp = await client.put(f"{endpoint}/json/new?{quote(self.url, safe='')}")
                    if resp.status_code == 405:
                        resp = await client.get(f"{endpoint}/json/new?{quote(self.url, safe='')}")
                    resp.raise_for_status()
                    return resp.json()
                except Exception:
                    await asyncio.sleep(0.25)
        raise RuntimeError("Chrome DevTools endpoint not ready")

    async def call(self, method: str, params: dict | None = None) -> dict:
        if not self.ws:
            raise RuntimeError("Chrome websocket is not connected")
        self.msgid += 1
        msgid = self.msgid
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self.pending[msgid] = future
        await self.ws.send(json.dumps({"id": msgid, "method": method, "params": params or {}}))
        return await future

    async def _read_loop(self) -> None:
        assert self.ws is not None
        while True:
            raw = await self.ws.recv()
            data = json.loads(raw)
            msgid = data.get("id")
            if msgid in self.pending:
                future = self.pending.pop(msgid)
                if "error" in data:
                    future.set_exception(RuntimeError(data["error"]))
                else:
                    future.set_result(data.get("result", {}))
                continue
            if data.get("method") == "Page.screencastFrame":
                params = data.get("params", {})
                self.frame_index += 1
                frame_path = self.frame_dir / f"frame_{self.frame_index:05d}.jpg"
                frame_path.write_bytes(base64.b64decode(params.get("data", "")))
                await self.ws.send(json.dumps({
                    "id": 100000 + self.frame_index,
                    "method": "Page.screencastFrameAck",
                    "params": {"sessionId": params.get("sessionId")},
                }))

    def export_mp4(self) -> None:
        frames = sorted(self.frame_dir.glob("frame_*.jpg"))
        if not frames:
            print(f"[record] 未找到浏览器录制帧：{self.frame_dir}")
            return
        ffmpeg = self.ffmpeg_path or shutil.which("ffmpeg")
        if not ffmpeg:
            print(f"[record] 已保存 {len(frames)} 帧浏览器录制画面，但未找到 ffmpeg，无法合成 MP4。")
            print(f"[record] frames: {self.frame_dir}")
            return
        self.mp4_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            ffmpeg,
            "-y",
            "-framerate",
            str(self.fps),
            "-start_number",
            "1",
            "-i",
            str(self.frame_dir / "frame_%05d.jpg"),
            "-vf",
            "scale=390:844:force_original_aspect_ratio=decrease,pad=390:844:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p",
            *video_encoder_args(ffmpeg),
            "-r",
            "30",
            str(self.mp4_path),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            print(f"[record] saved {self.mp4_path}")
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or str(exc)).strip()
            print(f"[record] ffmpeg 生成浏览器录制 MP4 失败：{detail}")


class DemoClient:
    def __init__(
        self,
        server: str,
        sn: str,
        auto: bool = False,
        delay: float = 0.5,
        play_voice: bool = False,
        image_url: str = DEFAULT_PANEL_IMAGE_URL,
        screenshot_dir: str = "",
        chrome_path: str = r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        screenshot_delay: float = 1.0,
        chrome_headed: bool = False,
        web_nav_visible: bool | None = None,
        record_mp4: bool = False,
        mp4_path: str = "",
        ffmpeg_path: str = "",
        mp4_fps: float = 1.0,
        record_browser: bool = False,
        browser_record_dir: str = "",
        browser_mp4_path: str = "",
        browser_record_fps: float = 8.0,
        browser_record_port: int = 9400,
        flow_output_config: str = "",
    ) -> None:
        self.server = server.rstrip("/")
        self.sn = sn
        self.auto = auto
        self.delay = delay
        self.play_voice = play_voice
        self.image_url = image_url
        if screenshot_dir:
            self.screenshot_dir = Path(screenshot_dir)
        elif record_mp4:
            self.screenshot_dir = Path("app/data/events/screenshots") / f"demo_recording_{int(time.time())}"
        else:
            self.screenshot_dir = None
        self.chrome_path = chrome_path
        self.screenshot_delay = screenshot_delay
        self.chrome_headed = chrome_headed
        self.web_nav_visible = web_nav_visible
        self.record_mp4 = record_mp4
        self.mp4_path = Path(mp4_path) if mp4_path else None
        self.ffmpeg_path = ffmpeg_path
        self.mp4_fps = mp4_fps
        self.record_browser = record_browser
        self.browser_record_dir = Path(browser_record_dir) if browser_record_dir else Path("app/data/events/recordings") / f"browser_recording_{int(time.time())}"
        self.browser_mp4_path = browser_mp4_path
        self.browser_record_fps = browser_record_fps
        self.browser_record_port = browser_record_port
        self.flow_outputs = self.load_flow_outputs(flow_output_config)
        self.ws_base = self.server.replace("http://", "ws://").replace("https://", "wss://")
        self.assets = Path(__file__).parent / "demo_assets"
        self.received = self.assets / "received_voice"
        self.assets.mkdir(parents=True, exist_ok=True)
        self.received.mkdir(parents=True, exist_ok=True)
        self.cmd_ws = None
        self.voice_ws = None
        self.web_ws = None
        self.voice_index = 0
        self.voice_buffer: list[bytes] = []
        self.ack_waiters: dict[int, asyncio.Future] = {}
        self.voice_done = asyncio.Event()
        self.web_events: list[dict[str, Any]] = []
        self.web_text_buffer = ""

    def load_flow_outputs(self, config_path: str) -> dict[str, dict[str, Any]]:
        if not config_path:
            return {}
        path = Path(config_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        if "steps" in data:
            data = data["steps"]
        if not isinstance(data, dict):
            raise ValueError("--flow-output-config 必须是 JSON 对象，或包含 steps 对象。")
        loaded: dict[str, dict[str, Any]] = {}
        for key, value in data.items():
            if not isinstance(value, dict):
                raise ValueError(f"流程配置 {key} 必须是 JSON 对象。")
            loaded[str(key)] = value
        print(f"[config] loaded flow output config: {path}")
        return loaded

    def flow_output(self, key: str) -> dict[str, Any]:
        default = DEFAULT_FLOW_OUTPUTS[key]
        override = self.flow_outputs.get(key, {})
        return {**default, **override}

    async def run(self) -> None:
        if not self.auto and msvcrt is None:
            raise RuntimeError("demo_client 当前需要 Windows msvcrt 才能使用空格逐步推进。")

        await self.ensure_assets()
        cmd_url = f"{self.ws_base}/{self.sn}/cmd"
        voice_url = f"{self.ws_base}/{self.sn}/voice"
        web_url = f"{self.ws_base}/ws/web/{self.sn}"
        print(f"Connecting cmd: {cmd_url}")
        print(f"Connecting voice: {voice_url}")
        print(f"Connecting web events: {web_url}")

        recorder = None
        if self.record_browser:
            recorder = ChromeBrowserRecorder(
                chrome_path=self.chrome_path,
                url=self.web_url(),
                output_dir=self.browser_record_dir,
                ffmpeg_path=self.ffmpeg_path,
                mp4_path=self.browser_mp4_path,
                fps=self.browser_record_fps,
                headed=self.chrome_headed,
                port=self.browser_record_port,
            )
            await recorder.start()

        try:
            async with websockets.connect(cmd_url) as cmd_ws, websockets.connect(voice_url) as voice_ws, websockets.connect(web_url) as web_ws:
                self.cmd_ws = cmd_ws
                self.voice_ws = voice_ws
                self.web_ws = web_ws
                voice_task = asyncio.create_task(self.listen_voice())
                cmd_task = asyncio.create_task(self.listen_cmd())
                web_task = asyncio.create_task(self.listen_web())
                try:
                    steps = self.steps()
                    for index, step in enumerate(steps, start=1):
                        print(f"\n[{index}/{len(steps)}] {step['title']}")
                        if self.auto:
                            await asyncio.sleep(self.delay)
                        else:
                            print("按空格执行当前步骤，按 Q 退出。")
                            key = await asyncio.to_thread(msvcrt.getch)
                            if key in (b"q", b"Q"):
                                break
                            if key != b" ":
                                print("已跳过：不是空格键。")
                                continue
                        await step["action"]()
                        await self.capture_step(index, str(step["title"]))
                        print("Step sent.")
                    self.export_mp4()
                finally:
                    voice_task.cancel()
                    cmd_task.cancel()
                    web_task.cancel()
                    self.flush_voice()
        finally:
            if recorder:
                await recorder.stop()

    async def listen_cmd(self) -> None:
        while True:
            msg = await self.cmd_ws.recv()
            print(f"[cmd] {msg}")
            try:
                payload = json.loads(msg)
            except json.JSONDecodeError:
                continue
            if payload.get("cmd") in {"voice_end", "voice_cancel"}:
                self.flush_voice()
            if payload.get("cmd") == "ack":
                msgid = payload.get("msgid")
                waiter = self.ack_waiters.pop(msgid, None)
                if waiter and not waiter.done():
                    waiter.set_result(payload)
            if payload.get("cmd") == "voice_end":
                self.voice_done.set()

    async def listen_voice(self) -> None:
        while True:
            msg = await self.voice_ws.recv()
            if isinstance(msg, bytes):
                self.voice_buffer.append(msg)

    async def listen_web(self) -> None:
        while True:
            raw = await self.web_ws.recv()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            self.web_events.append(payload)
            self.web_events = self.web_events[-300:]
            event_type = payload.get("type")
            if event_type in {"agent_delta", "agent_thought_delta"}:
                self.web_text_buffer += str(payload.get("content", ""))
            elif event_type == "tts_start":
                self.web_text_buffer += str(payload.get("text", ""))

    async def wait_for_web_text(self, text: str, timeout: float = 60.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if text in self.web_text_buffer:
                print(f"[wait] observed web text: {text}")
                return
            await asyncio.sleep(0.1)
        print(f"[wait] timeout waiting for web text: {text}")

    def flush_voice(self) -> None:
        if not self.voice_buffer:
            return
        self.voice_index += 1
        pcm_path = self.received / f"voice-{self.voice_index:03d}.pcm"
        wav_path = self.received / f"voice-{self.voice_index:03d}.wav"
        pcm = b"".join(self.voice_buffer)
        pcm_path.write_bytes(pcm)
        self.write_wav(wav_path, pcm)
        self.voice_buffer.clear()
        print(f"[voice] saved {pcm_path}")
        print(f"[voice] saved {wav_path}")
        if self.play_voice:
            self.play_wav(wav_path)

    def write_wav(self, path: Path, pcm: bytes) -> None:
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)
            wav_file.writeframes(pcm)

    def play_wav(self, path: Path) -> None:
        if winsound is None:
            print("[voice] 当前平台不支持 winsound，已跳过播放。")
            return
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        print(f"[voice] playing {path}")

    def web_url(self) -> str:
        params = {"sn": self.sn}
        if self.web_nav_visible is not None:
            params["nav"] = "1" if self.web_nav_visible else "0"
        if self.record_browser:
            params["autoscroll"] = "1"
        return f"{self.server}/?{urlencode(params)}"

    def export_mp4(self) -> None:
        if not self.record_mp4:
            return
        if self.screenshot_dir is None:
            print("[record] 未配置截图目录，无法生成 MP4。")
            return
        frames = sorted(self.screenshot_dir.glob("step_*.png"))
        if not frames:
            print(f"[record] 未找到截图帧：{self.screenshot_dir}")
            return
        ffmpeg = self.ffmpeg_path or shutil.which("ffmpeg")
        if not ffmpeg:
            print("[record] 未找到 ffmpeg，跳过 MP4 生成。请安装 ffmpeg 或使用 --ffmpeg-path 指定路径。")
            return
        output = self.mp4_path or (self.screenshot_dir / "demo_flow.mp4")
        output.parent.mkdir(parents=True, exist_ok=True)
        input_pattern = str(self.screenshot_dir / "step_%02d.png")
        fps = max(self.mp4_fps, 0.1)
        cmd = [
            ffmpeg,
            "-y",
            "-framerate",
            str(fps),
            "-start_number",
            "1",
            "-i",
            input_pattern,
            "-vf",
            "scale=390:844:force_original_aspect_ratio=decrease,pad=390:844:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p",
            *video_encoder_args(ffmpeg),
            "-r",
            "30",
            str(output),
        ]
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            if result.stderr.strip():
                print(result.stderr.strip().splitlines()[-1])
            print(f"[record] saved {output}")
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or str(exc)).strip()
            print(f"[record] ffmpeg 生成 MP4 失败：{detail}")

    async def capture_step(self, index: int, title: str) -> None:
        if self.screenshot_dir is None:
            return
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        if not Path(self.chrome_path).exists():
            print(f"[screenshot] Chrome not found: {self.chrome_path}")
            return
        await asyncio.sleep(self.screenshot_delay)
        path = self.screenshot_dir / f"step_{index:02d}.png"
        profile_dir = self.screenshot_dir / "chrome_profile"
        url = self.web_url()
        port = 9300 + index
        args = [
            self.chrome_path,
            "--disable-gpu",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-sync",
            "--no-first-run",
            "--no-default-browser-check",
            "--hide-scrollbars",
            "--window-size=390,844",
            f"--user-data-dir={profile_dir}",
            f"--remote-debugging-port={port}",
            "about:blank",
        ]
        if not self.chrome_headed:
            args.insert(1, "--headless=new")
        proc = None
        try:
            proc = await asyncio.to_thread(subprocess.Popen, args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            await self.capture_with_cdp(port, url, path)
            print(f"[screenshot] saved {path} ({title})")
        except Exception as exc:
            print(f"[screenshot] failed step={index}: {exc}")
        finally:
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    await asyncio.to_thread(proc.wait, timeout=5)
                except Exception:
                    proc.kill()

    async def capture_with_cdp(self, port: int, url: str, path: Path) -> None:
        endpoint = f"http://127.0.0.1:{port}"
        async with httpx.AsyncClient(timeout=10) as client:
            for _ in range(40):
                try:
                    resp = await client.put(f"{endpoint}/json/new?{quote(url, safe='')}")
                    if resp.status_code == 405:
                        resp = await client.get(f"{endpoint}/json/new?{quote(url, safe='')}")
                    resp.raise_for_status()
                    tab = resp.json()
                    break
                except Exception:
                    await asyncio.sleep(0.25)
            else:
                raise RuntimeError("Chrome DevTools endpoint not ready")

        ws_url = tab.get("webSocketDebuggerUrl")
        if not ws_url:
            raise RuntimeError("Chrome did not return webSocketDebuggerUrl")

        async with websockets.connect(ws_url, max_size=8 * 1024 * 1024) as ws:
            msgid = 0

            async def call(method: str, params: dict | None = None) -> dict:
                nonlocal msgid
                msgid += 1
                await ws.send(json.dumps({"id": msgid, "method": method, "params": params or {}}))
                while True:
                    raw = await ws.recv()
                    data = json.loads(raw)
                    if data.get("id") == msgid:
                        if "error" in data:
                            raise RuntimeError(data["error"])
                        return data.get("result", {})

            await call("Page.enable")
            await call("Emulation.setDeviceMetricsOverride", {
                "width": 390,
                "height": 844,
                "deviceScaleFactor": 2,
                "mobile": True,
            })
            await call("Page.navigate", {"url": url})
            await asyncio.sleep(max(1.0, self.screenshot_delay))
            result = await call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
            data = base64.b64decode(result["data"])
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)

    def steps(self) -> list[dict[str, StepAction | str]]:
        return [
            self.demo_payload_step("helmet_start"),
            self.upload_step("ask_flow"),
            self.demo_payload_step("agent_flow"),
            self.demo_payload_step("photo_prompt"),
            self.upload_step("upload_panel_image"),
            self.demo_payload_step("analysis"),
            self.demo_payload_step("rectification_advice"),
            self.upload_step("confirm_rectification"),
            self.demo_payload_step("rectification_image"),
            self.upload_step("repeat"),
            self.upload_step("empty_pcm"),
            self.upload_step("non_site_image"),
            self.upload_step("blurry_image"),
            {
                "title": str(self.flow_output("demo_end").get("step_title", "演示结束")),
                "action": self.demo_end,
            },
        ]

    def demo_payload_step(self, key: str) -> dict[str, StepAction | str]:
        cfg = self.flow_output(key)
        return {
            "title": str(cfg.get("step_title") or cfg.get("title") or key),
            "action": lambda cfg=cfg: self.demo_step(
                int(cfg["step"]),
                str(cfg["scene"]),
                str(cfg["title"]),
                str(cfg["text"]),
                image_url=str(cfg.get("image_url", "")),
                image_role=str(cfg.get("image_role", "agent")),
            ),
        }

    def upload_step(self, key: str) -> dict[str, StepAction | str]:
        cfg = self.flow_output(key)
        return {
            "title": str(cfg.get("step_title") or key),
            "action": lambda cfg=cfg, key=key: self.upload_step_action(key, cfg),
        }

    async def upload_step_action(self, key: str, cfg: dict[str, Any]) -> None:
        if key == "confirm_rectification":
            await self.wait_for_web_text(FOLLOW_UP_PROMPT_TEXT)
        await self.upload_and_notify(
            str(cfg["asset"]),
            str(cfg["cmd"]),
            int(cfg["msgid"]),
        )

    async def demo_step(
        self,
        step: int,
        scene: str,
        title: str,
        text: str,
        image_url: str = "",
        image_role: str = "agent",
    ) -> None:
        payload = {
            "msgid": int(time.time() * 1000) if image_url else 9000 + step,
            "cmd": "demo_step",
            "step": step,
            "scene": scene,
            "title": title,
            "text": text,
        }
        if image_url:
            payload["image_url"] = image_url
            payload["image_role"] = image_role
        await self.send_cmd(payload)

    async def demo_end(self) -> None:
        await self.send_cmd({"msgid": 9010, "cmd": "demo_end", "msg": "OVER"})

    async def upload_and_notify(self, asset_name: str, cmd: str, msgid: int) -> None:
        src = self.assets / asset_name
        suffix = src.suffix
        target_name = f"{self.sn}-{int(time.time() * 1000)}{suffix}"
        print(f"Uploading {src.name} as {target_name}")
        data = src.read_bytes()
        self.voice_done.clear()
        timeout = httpx.Timeout(30.0, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{self.server}/api/upload", files={"file": (target_name, data)})
            resp.raise_for_status()
        waiter = asyncio.get_running_loop().create_future()
        self.ack_waiters[msgid] = waiter
        await self.send_cmd({"msgid": msgid, "cmd": cmd, "file": target_name, "msg": "success"})
        try:
            await asyncio.wait_for(waiter, timeout=30)
        except asyncio.TimeoutError:
            print(f"ACK timeout for msgid={msgid}; continuing demo flow.")
        try:
            await asyncio.wait_for(self.voice_done.wait(), timeout=20)
        except asyncio.TimeoutError:
            print("No voice_end observed for this upload; continuing.")

    async def send_cmd(self, payload: dict) -> None:
        text = json.dumps(payload, ensure_ascii=False)
        await self.cmd_ws.send(text)
        print(f"[send] {text}")

    async def ensure_assets(self) -> None:
        self.make_pcm(self.assets / "step_ask_flow.pcm", "ask_flow")
        self.make_pcm(self.assets / "step_confirm.pcm", "confirm")
        self.make_pcm(self.assets / "step_repeat.pcm", "repeat")
        (self.assets / "empty.pcm").write_bytes(b"")

        for name in ("step_upload_panel.jpeg", "non_site.jpeg", "blurry_panel.jpeg"):
            path = self.assets / name
            marker = b""
            if name == "step_upload_panel.jpeg":
                downloaded = await self.download_panel_image(path)
                if downloaded:
                    continue
            elif name == "non_site.jpeg":
                marker = b"NON_SITE_MARKER"
            elif name == "blurry_panel.jpeg":
                marker = b"BLURRY_MARKER"
            path.write_bytes(JPEG_1X1 + (b"0" * 2048) + marker)

    async def download_panel_image(self, path: Path) -> bool:
        if not self.image_url:
            return False
        try:
            timeout = httpx.Timeout(30.0, connect=10.0)
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                resp = await client.get(self.image_url)
                resp.raise_for_status()
            data = resp.content
            content_type = resp.headers.get("content-type", "").lower()
            is_image = content_type.startswith("image/") or data.startswith((b"\xff\xd8\xff", b"\x89PNG"))
            if not is_image or len(data) < 100:
                raise RuntimeError(f"响应不是有效图片：content_type={content_type} size={len(data)}")
            path.write_bytes(data)
            print(f"[asset] downloaded fixed panel image: {self.image_url} -> {path} ({len(data)} bytes)")
            return True
        except Exception as exc:
            print(f"[asset] fixed panel image download failed: {exc}; fallback to generated placeholder")
            return False

    def make_pcm(self, path: Path, label: str) -> None:
        sample_rate = 16000
        frames = bytearray()
        frequency = {"ask_flow": 440, "confirm": 520, "repeat": 660}.get(label, 440)
        for i in range(sample_rate):
            value = int(1500 * math.sin(2 * math.pi * frequency * i / sample_rate))
            frames.extend(struct.pack("<h", value))
        frames.extend(label.upper().encode("ascii"))
        path.write_bytes(bytes(frames))


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="http://127.0.0.1:18787")
    parser.add_argument("--sn", default="rv1126b")
    parser.add_argument("--auto", action="store_true")
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--play-voice", action="store_true", help="收到 TTS 后保存 WAV 并使用 Windows 音频异步播放")
    parser.add_argument("--image-url", default=DEFAULT_PANEL_IMAGE_URL, help="场景3上传使用的固定图片 URL")
    parser.add_argument("--screenshot-dir", default="", help="每一步执行后使用 Chrome headless 保存 Web 页面截图")
    parser.add_argument("--chrome-path", default=r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    parser.add_argument("--screenshot-delay", type=float, default=1.0)
    parser.add_argument("--chrome-headed", action="store_true", help="截图时启动可见 Chrome 窗口，适用于 headless 被禁用的环境")
    nav_group = parser.add_mutually_exclusive_group()
    nav_group.add_argument("--hide-web-nav", action="store_true", help="截图/录制 Web 页面时隐藏历史和文档按钮")
    nav_group.add_argument("--show-web-nav", action="store_true", help="截图/录制 Web 页面时显示历史和文档按钮")
    parser.add_argument("--record-mp4", action="store_true", help="流程结束后使用截图帧合成 MP4，需要 ffmpeg")
    parser.add_argument("--mp4-path", default="", help="MP4 输出路径，默认保存为截图目录下 demo_flow.mp4")
    parser.add_argument("--ffmpeg-path", default="", help="ffmpeg 可执行文件路径；不填则从 PATH 查找")
    parser.add_argument("--mp4-fps", type=float, default=1.0, help="截图帧合成视频时每秒帧数")
    parser.add_argument("--record-browser", action="store_true", help="使用 Chrome DevTools 连续录制 Web 页面，流程结束后尝试合成 MP4")
    parser.add_argument("--browser-record-dir", default="", help="浏览器连续录制帧保存目录")
    parser.add_argument("--browser-mp4-path", default="", help="浏览器连续录制 MP4 输出路径，默认保存到录制目录")
    parser.add_argument("--browser-record-fps", type=float, default=8.0, help="浏览器连续录制导出 MP4 的帧率")
    parser.add_argument("--browser-record-port", type=int, default=9400, help="浏览器连续录制使用的 Chrome DevTools 端口")
    parser.add_argument("--flow-output-config", default="", help="演示流程输出覆盖配置 JSON，可配置每一步标题、文本、图片和上传参数")
    args = parser.parse_args()
    web_nav_visible = None
    if args.hide_web_nav:
        web_nav_visible = False
    elif args.show_web_nav:
        web_nav_visible = True
    await DemoClient(
        args.server,
        args.sn,
        auto=args.auto,
        delay=args.delay,
        play_voice=args.play_voice,
        image_url=args.image_url,
        screenshot_dir=args.screenshot_dir,
        chrome_path=args.chrome_path,
        screenshot_delay=args.screenshot_delay,
        chrome_headed=args.chrome_headed,
        web_nav_visible=web_nav_visible,
        record_mp4=args.record_mp4,
        mp4_path=args.mp4_path,
        ffmpeg_path=args.ffmpeg_path,
        mp4_fps=args.mp4_fps,
        record_browser=args.record_browser,
        browser_record_dir=args.browser_record_dir,
        browser_mp4_path=args.browser_mp4_path,
        browser_record_fps=args.browser_record_fps,
        browser_record_port=args.browser_record_port,
        flow_output_config=args.flow_output_config,
    ).run()


if __name__ == "__main__":
    asyncio.run(main())
