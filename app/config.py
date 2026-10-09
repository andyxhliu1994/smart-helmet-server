from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_env_file() -> None:
    env_path = Path(".env")
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_env_file()


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    value = _env(name, "1" if default else "0").strip().lower()
    return value not in {"0", "false", "no", "off", "hide", "hidden"}


def _csv(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in _env(name, default).split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    app_host: str = _env("APP_HOST", "0.0.0.0")
    app_port: int = _int("APP_PORT", 18787)
    app_mode: str = _env("APP_MODE", "demo")
    default_sn: str = _env("DEFAULT_SN", "rv1126b")
    upload_dir: Path = Path(_env("UPLOAD_DIR", "app/uploads"))
    data_dir: Path = Path(_env("DATA_DIR", "app/data"))
    admin_token: str = _env("ADMIN_TOKEN", "")
    web_nav_visible: bool = _bool("WEB_NAV_VISIBLE", True)

    asr_provider: str = _env("ASR_PROVIDER", "mock")
    asr_model: str = _env("ASR_MODEL", "paraformer-zh")
    asr_device: str = _env("ASR_DEVICE", "cpu")
    asr_min_text_length: int = _int("ASR_MIN_TEXT_LENGTH", 2)
    asr_low_confidence_threshold: float = float(_env("ASR_LOW_CONFIDENCE_THRESHOLD", "0.4"))
    asr_invalid_texts: list[str] = None  # type: ignore[assignment]
    external_upload_url: str = _env("EXTERNAL_UPLOAD_URL", "")
    external_upload_token: str = _env("EXTERNAL_UPLOAD_TOKEN", "")
    asr_gateway_url: str = _env("ASR_GATEWAY_URL", "")
    asr_gateway_token: str = _env("ASR_GATEWAY_TOKEN", "")
    gateway_timeout_seconds: int = _int("GATEWAY_TIMEOUT_SECONDS", 30)

    tts_provider: str = _env("TTS_PROVIDER", "mock")
    tts_model: str = _env("TTS_MODEL", "iic/speech_sambert-hifigan_tts_zh-cn_16k")
    tts_device: str = _env("TTS_DEVICE", "cpu")
    tts_chunk_size: int = _int("TTS_CHUNK_SIZE", 3200)
    tts_gateway_url: str = _env("TTS_GATEWAY_URL", "")
    tts_gateway_token: str = _env("TTS_GATEWAY_TOKEN", "")
    tts_gateway_response_format: str = _env("TTS_GATEWAY_RESPONSE_FORMAT", "pcm")
    tts_max_sentence_chars: int = _int("TTS_MAX_SENTENCE_CHARS", 80)

    llm_provider: str = _env("LLM_PROVIDER", "mock")
    llm_base_url: str = _env("LLM_BASE_URL", "")
    llm_api_key: str = _env("LLM_API_KEY", "")
    llm_model: str = _env("LLM_MODEL", "Qwen3-VL-32B-Instruct")
    vision_model: str = _env("VISION_MODEL", "Qwen3-VL-32B-Instruct")
    use_dynamic_reply: bool = _bool("USE_DYNAMIC_REPLY", True)

    pcm_sample_rate: int = _int("PCM_SAMPLE_RATE", 16000)
    pcm_channels: int = _int("PCM_CHANNELS", 1)
    pcm_sample_width: int = _int("PCM_SAMPLE_WIDTH", 16)

    cors_origins: list[str] = None  # type: ignore[assignment]
    device_queue_max_size: int = _int("DEVICE_QUEUE_MAX_SIZE", 20)

    max_pcm_size_mb: int = _int("MAX_PCM_SIZE_MB", 20)
    max_jpeg_size_mb: int = _int("MAX_JPEG_SIZE_MB", 10)
    max_h264_size_mb: int = _int("MAX_H264_SIZE_MB", 200)

    asr_failed_reply: str = _env("ASR_FAILED_REPLY", "没有听清，请您再说一遍。")
    local_flow_not_found_reply: str = _env("LOCAL_FLOW_NOT_FOUND_REPLY", "本地未找到对应流程，正在联网检索。")
    search_failed_reply: str = _env("SEARCH_FAILED_REPLY", "联网检索失败，请稍后重试或联系管理员补充文档。")

    def __post_init__(self) -> None:
        object.__setattr__(self, "asr_invalid_texts", _csv("ASR_INVALID_TEXTS", "嗯,啊,呃,哦"))
        object.__setattr__(self, "cors_origins", _csv("CORS_ORIGINS", "http://localhost:18787,http://127.0.0.1:18787"))


settings = Settings()
