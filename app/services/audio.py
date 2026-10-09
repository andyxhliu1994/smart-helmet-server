from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from app.config import settings


class AudioConversionError(RuntimeError):
    pass


class AudioService:
    def pcm_from_upload_bytes(self, filename: str | None, data: bytes) -> bytes:
        suffix = Path(filename or "").suffix.lower()
        if suffix == ".mp3":
            return self.mp3_bytes_to_pcm(data)
        return self.strip_wav_header(data)

    def mp3_file_to_pcm_file(self, source: Path, target: Path | None = None) -> Path:
        if target is None:
            target = settings.upload_dir / "pcm" / f"{source.stem}.pcm"
        target.parent.mkdir(parents=True, exist_ok=True)
        pcm = self._run_ffmpeg(source)
        target.write_bytes(pcm)
        return target

    def mp3_bytes_to_pcm(self, data: bytes) -> bytes:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)
        try:
            return self._run_ffmpeg(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)

    def strip_wav_header(self, data: bytes) -> bytes:
        if data[:4] != b"RIFF":
            return data
        idx = data.find(b"data")
        if idx == -1:
            return data
        return data[idx + 8:]

    def _run_ffmpeg(self, source: Path) -> bytes:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise AudioConversionError("未找到 ffmpeg，无法将 MP3 转为设备需要的 PCM")

        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-ac",
            str(settings.pcm_channels),
            "-ar",
            str(settings.pcm_sample_rate),
            "pipe:1",
        ]
        completed = subprocess.run(cmd, capture_output=True, check=False)
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="ignore").strip()
            raise AudioConversionError(f"MP3 转 PCM 失败：{detail or 'ffmpeg 返回错误'}")
        if not completed.stdout:
            raise AudioConversionError("MP3 转 PCM 失败：ffmpeg 未输出音频数据")
        return completed.stdout


audio_service = AudioService()
