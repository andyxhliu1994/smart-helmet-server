from __future__ import annotations

import re
import wave
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.services.logger import logger


class ASRService:
    def __init__(self) -> None:
        self.provider = settings.asr_provider
        self.model = None
        if self.provider == "funasr":
            try:
                from funasr import AutoModel

                self.model = AutoModel(model=settings.asr_model, device=settings.asr_device)
                logger.info("[模型加载] FunASR 模型加载完成：%s", settings.asr_model)
            except Exception as exc:
                logger.error("[模型加载] FunASR 加载失败，降级为 mock：%s", exc)
                self.provider = "mock"

    async def transcribe_pcm(self, pcm_path: str) -> str:
        path = Path(pcm_path)
        logger.info("[语音识别] 开始识别 %s", path.name)
        if self.provider == "http_gateway":
            text = await self._transcribe_http_gateway(path)
        elif self.provider == "funasr" and self.model is not None:
            wav_path = path.with_suffix(".wav")
            self._pcm_to_wav(path, wav_path)
            result = self.model.generate(input=str(wav_path))
            text = self._extract_text(result)
        else:
            text = self._mock_text(path)
        logger.info("[语音识别] 识别结果：%s", text or "空")
        return text

    def is_valid_text(self, text: str) -> bool:
        clean = re.sub(r"[\s，。！？、,.!?]", "", text)
        if len(clean) < settings.asr_min_text_length:
            return False
        return clean not in settings.asr_invalid_texts

    async def _transcribe_http_gateway(self, path: Path) -> str:
        if not settings.external_upload_url:
            raise RuntimeError("ASR_PROVIDER=http_gateway 时必须配置 EXTERNAL_UPLOAD_URL")
        if not settings.asr_gateway_url:
            raise RuntimeError("ASR_PROVIDER=http_gateway 时必须配置 ASR_GATEWAY_URL")

        async with httpx.AsyncClient(timeout=settings.gateway_timeout_seconds) as client:
            upload_headers = self._token_headers(settings.external_upload_token)
            logger.info("[语音识别] 上传 PCM 到外部文件网关：%s", path.name)
            upload_resp = await client.post(
                settings.external_upload_url,
                headers=upload_headers,
                files={"file": (path.name, path.read_bytes(), "application/octet-stream")},
            )
            upload_resp.raise_for_status()
            source_url = self._extract_url(upload_resp)
            if not source_url:
                raise RuntimeError("外部文件网关未返回可识别的 url 字段")

            asr_headers = {"Content-Type": "application/json", **self._token_headers(settings.asr_gateway_token)}
            logger.info("[语音识别] 调用外部 FunASR 网关：%s", source_url)
            asr_resp = await client.post(settings.asr_gateway_url, headers=asr_headers, json={"url": source_url})
            asr_resp.raise_for_status()
            text = self._extract_text_from_response(asr_resp)
            if not text:
                logger.warning("[语音识别] 外部 FunASR 网关未返回有效文字")
            return text

    def _pcm_to_wav(self, pcm_path: Path, wav_path: Path) -> None:
        data = pcm_path.read_bytes()
        with wave.open(str(wav_path), "wb") as wav_file:
            wav_file.setnchannels(settings.pcm_channels)
            wav_file.setsampwidth(settings.pcm_sample_width // 8)
            wav_file.setframerate(settings.pcm_sample_rate)
            wav_file.writeframes(data)

    def _extract_text(self, result: object) -> str:
        if isinstance(result, list) and result:
            first = result[0]
            if isinstance(first, dict):
                return str(first.get("text", "")).strip()
        if isinstance(result, dict):
            return str(result.get("text", "")).strip()
        return str(result).strip()

    def _extract_url(self, response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            text = response.text.strip()
            return text if text.startswith(("http://", "https://")) else ""
        value = self._find_first(payload, {"url", "file_url", "fileUrl", "download_url", "downloadUrl", "src"})
        return value if isinstance(value, str) else ""

    def _extract_text_from_response(self, response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return response.text.strip()
        value = self._find_first(payload, {"text", "result", "content", "sentence", "transcript"})
        return str(value).strip() if value is not None else ""

    def _find_first(self, value: Any, keys: set[str]) -> Any:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in keys and item:
                    if isinstance(item, (dict, list)):
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

    def _token_headers(self, token: str) -> dict[str, str]:
        return {"token": token} if token else {}

    def _mock_text(self, path: Path) -> str:
        name = path.name.lower()
        data = path.read_bytes()
        if "empty" in name or path.stat().st_size == 0:
            return ""
        marker_text = data.decode("latin1", errors="ignore")
        if "REPEAT" in marker_text:
            return "再说一遍。"
        if "CONFIRM" in marker_text:
            return "收到，我们会立即整改，谢谢！"
        return "今天要检查三级配电箱线路，请告诉我检查流程。"


asr_service = ASRService()
