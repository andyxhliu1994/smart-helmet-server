from __future__ import annotations

import base64
import asyncio
import math
import re
import struct
import time
from typing import Any, AsyncIterator

import httpx
import requests

from app.config import settings
from app.services.logger import logger


class TTSService:
    SENTENCE_ENDINGS = set(".!?;\u3002\uff01\uff1f\uff1b")
    SOFT_BREAKS = set(",:\u3001\uff0c\uff1a")

    def __init__(self) -> None:
        self.provider = settings.tts_provider
        self.pipeline = None
        if self.provider == "modelscope":
            try:
                from modelscope.pipelines import pipeline
                from modelscope.utils.constant import Tasks

                self.pipeline = pipeline(task=Tasks.text_to_speech, model=settings.tts_model)
                logger.info("[模型加载] TTS 模型加载完成：%s", settings.tts_model)
            except Exception as exc:
                logger.error("[模型加载] TTS 加载失败，降级为 mock：%s", exc)
                self.provider = "mock"

    async def _synthesize_pcm_legacy(self, text: str) -> bytes:
        logger.info("[语音合成] 开始合成：%s", text)
        if self.provider == "http_gateway":
            return await self._synthesize_http_gateway(text)
        if self.provider == "modelscope" and self.pipeline is not None:
            result = self.pipeline(input=text)
            audio = result.get("output_pcm") or result.get("output_wav") or b""
            if isinstance(audio, str):
                data = open(audio, "rb").read()
            else:
                data = bytes(audio)
            return self._strip_wav_header(data)
        return self._mock_pcm(text)

    async def synthesize_pcm(self, text: str) -> bytes:
        parts = await self.synthesize_pcm_parts(text)
        return self.join_pcm_segments([pcm for _, pcm in parts])

    def join_pcm_segments(self, segments: list[bytes]) -> bytes:
        if not segments:
            return b""
        silence = self._silence_pcm(120)
        joined = bytearray()
        for index, pcm in enumerate(segments):
            if index and silence:
                joined.extend(silence)
            joined.extend(pcm)
        return bytes(joined)

    async def synthesize_pcm_parts(self, text: str, max_chars: int | None = None) -> list[tuple[str, bytes]]:
        sentences = self.split_sentences(text, max_chars=max_chars)
        if not sentences:
            return []
        if len(sentences) > 1:
            logger.info("[TTS] split text into %s sentence segments", len(sentences))

        parts: list[tuple[str, bytes]] = []
        for index, sentence in enumerate(sentences, start=1):
            logger.info("[TTS] synthesizing segment %s/%s: %s", index, len(sentences), sentence)
            parts.append((sentence, await self._synthesize_pcm_segment(sentence)))
        return parts

    async def iter_synthesize_pcm_parts(self, text: str, max_chars: int | None = None) -> AsyncIterator[tuple[str, bytes]]:
        sentences = self.split_sentences(text, max_chars=max_chars)
        if not sentences:
            return
        if len(sentences) > 1:
            logger.info("[TTS] split text into %s sentence segments", len(sentences))

        for index, sentence in enumerate(sentences, start=1):
            logger.info("[TTS] synthesizing segment %s/%s: %s", index, len(sentences), sentence)
            yield sentence, await self._synthesize_pcm_segment(sentence)

    def split_sentences(self, text: str, max_chars: int | None = None) -> list[str]:
        max_chars = max(1, max_chars or settings.tts_max_sentence_chars)
        normalized = re.sub(r"[ \t]+", " ", text or "").strip()
        if not normalized:
            return []

        rough_parts: list[str] = []
        current: list[str] = []
        for char in normalized:
            if char in "\r\n":
                part = "".join(current).strip()
                if part:
                    rough_parts.append(part)
                current = []
                continue

            current.append(char)
            if char in self.SENTENCE_ENDINGS:
                part = "".join(current).strip()
                if part:
                    rough_parts.append(part)
                current = []

        tail = "".join(current).strip()
        if tail:
            rough_parts.append(tail)

        result: list[str] = []
        for part in rough_parts:
            result.extend(self._split_long_sentence(part, max_chars))
        return [part for part in result if part]

    def _split_long_sentence(self, text: str, max_chars: int) -> list[str]:
        parts: list[str] = []
        current = text.strip()
        while len(current) > max_chars:
            split_at = self._best_split_index(current, max_chars)
            parts.append(current[:split_at + 1].strip())
            current = current[split_at + 1:].strip()
        if current:
            parts.append(current)
        return parts

    def _best_split_index(self, text: str, max_chars: int) -> int:
        window = text[:max_chars]
        split_at = max((window.rfind(char) for char in self.SOFT_BREAKS), default=-1)
        if split_at >= max_chars // 3:
            return split_at
        return max_chars - 1

    async def _synthesize_pcm_segment(self, text: str) -> bytes:
        return await self._synthesize_pcm_legacy(text)

    async def _synthesize_http_gateway(self, text: str) -> bytes:
        if not settings.tts_gateway_url:
            raise RuntimeError("TTS_PROVIDER=http_gateway 时必须配置 TTS_GATEWAY_URL")
        last_exc: Exception | None = None
        for attempt in range(1, 3):
            try:
                return await self.synthesize_pcm_via_gateway(
                    text=text,
                    url=settings.tts_gateway_url,
                    token=settings.tts_gateway_token,
                    response_format=settings.tts_gateway_response_format,
                )
            except (requests.Timeout, requests.ConnectionError, requests.RequestException) as exc:
                last_exc = exc
                logger.warning("[语音合成] 外部 TTS 网关第 %s 次调用失败：%s", attempt, exc)
                if attempt < 2:
                    await asyncio.sleep(0.8)
        if last_exc:
            raise last_exc
        raise RuntimeError("外部 TTS 网关调用失败")

    async def synthesize_pcm_via_gateway(
        self,
        text: str,
        url: str,
        token: str = "",
        response_format: str = "pcm",
        verify_ssl: bool = True,
    ) -> bytes:
        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "Accept": "*/*",
        }
        if token:
            headers["token"] = token
        payload = {
            "input": text,
            "response_format": response_format,
        }
        data = await asyncio.to_thread(self._post_gateway_with_requests, url, headers, payload, verify_ssl)
        pcm = self._strip_wav_header(data)
        if not pcm:
            raise RuntimeError("外部 TTS 网关未返回有效音频")
        logger.info("[语音合成] 外部 TTS 网关返回音频字节数：%s", len(pcm))
        return pcm

    def _post_gateway_with_requests(self, url: str, headers: dict[str, str], payload: dict[str, Any], verify_ssl: bool) -> bytes:
        timeout = max(settings.gateway_timeout_seconds, 45)
        start = time.monotonic()
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=timeout,
            verify=verify_ssl,
        )
        logger.info("[语音合成] 外部 TTS 网关耗时 %.2fs", time.monotonic() - start)
        stripped = response.content.lstrip()[:64].lower()
        looks_like_html = stripped.startswith((b"<!doctype html", b"<html"))
        if response.status_code >= 400 and looks_like_html:
            preview = response.text[:200].replace("\r", "").replace("\n", " ")
            raise RuntimeError(f"HTTP {response.status_code}: {preview}")

        content_type = response.headers.get("content-type", "").lower()
        if "application/json" not in content_type:
            return response.content

        body = response.json()
        audio_value = self._find_first(body, {"audio", "data", "pcm", "content", "result"})
        if isinstance(audio_value, str):
            stripped_value = audio_value.strip()
            if stripped_value.startswith(("http://", "https://")):
                audio_resp = requests.get(stripped_value, timeout=settings.gateway_timeout_seconds, verify=verify_ssl)
                audio_resp.raise_for_status()
                return audio_resp.content
            try:
                return base64.b64decode(stripped_value)
            except Exception:
                return stripped_value.encode("latin1", errors="ignore")
        if isinstance(audio_value, list):
            return bytes(audio_value)
        if isinstance(audio_value, (bytes, bytearray)):
            return bytes(audio_value)

        audio_url = self._find_first(body, {"url", "file_url", "fileUrl", "download_url", "downloadUrl", "src"})
        if isinstance(audio_url, str) and audio_url.startswith(("http://", "https://")):
            audio_resp = requests.get(audio_url, timeout=settings.gateway_timeout_seconds, verify=verify_ssl)
            audio_resp.raise_for_status()
            return audio_resp.content
        return b""

    async def _extract_audio(self, client: httpx.AsyncClient, response: httpx.Response) -> bytes:
        content_type = response.headers.get("content-type", "").lower()
        if "application/json" not in content_type:
            return response.content

        payload = response.json()
        audio_value = self._find_first(payload, {"audio", "data", "pcm", "content", "result"})
        if isinstance(audio_value, str):
            stripped = audio_value.strip()
            if stripped.startswith(("http://", "https://")):
                audio_resp = await client.get(stripped)
                audio_resp.raise_for_status()
                return audio_resp.content
            try:
                return base64.b64decode(stripped)
            except Exception:
                return stripped.encode("latin1", errors="ignore")
        if isinstance(audio_value, list):
            return bytes(audio_value)
        if isinstance(audio_value, (bytes, bytearray)):
            return bytes(audio_value)

        url = self._find_first(payload, {"url", "file_url", "fileUrl", "download_url", "downloadUrl", "src"})
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            audio_resp = await client.get(url)
            audio_resp.raise_for_status()
            return audio_resp.content
        return b""

    def _find_first(self, value: Any, keys: set[str]) -> Any:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in keys and item:
                    if isinstance(item, dict):
                        found = self._find_first(item, keys)
                        if found:
                            return found
                    else:
                        return item
            for item in value.values():
                found = self._find_first(item, keys)
                if found:
                    return found
        elif isinstance(value, list):
            for item in value:
                found = self._find_first(item, keys)
                if found:
                    return found
        return None

    def _mock_pcm(self, text: str) -> bytes:
        duration = max(0.8, min(4.0, len(text) / 12))
        sample_count = int(settings.pcm_sample_rate * duration)
        frames = bytearray()
        for i in range(sample_count):
            value = int(1800 * math.sin(2 * math.pi * 440 * i / settings.pcm_sample_rate))
            frames.extend(struct.pack("<h", value))
        return bytes(frames)

    def _silence_pcm(self, duration_ms: int) -> bytes:
        bytes_per_sample = max(1, settings.pcm_sample_width // 8)
        frame_size = bytes_per_sample * max(1, settings.pcm_channels)
        frame_count = int(settings.pcm_sample_rate * duration_ms / 1000)
        return b"\x00" * frame_count * frame_size

    def _strip_wav_header(self, data: bytes) -> bytes:
        if data[:4] != b"RIFF":
            return data
        idx = data.find(b"data")
        if idx == -1:
            return data
        size_start = idx + 4
        pcm_start = size_start + 4
        return data[pcm_start:]


tts_service = TTSService()
