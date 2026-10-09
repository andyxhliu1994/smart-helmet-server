from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

from app.config import settings


FILE_RE = re.compile(r"^(?P<sn>[^/\\\s]+)-(?P<ts>\d+)\.(?P<ext>pcm|mp3|jpeg|jpg|h264)$", re.I)


@dataclass
class StoredFile:
    sn: str
    filename: str
    file_type: str
    path: Path
    size: int


class StorageService:
    def __init__(self) -> None:
        self.upload_dir = settings.upload_dir
        for name in ("pcm", "mp3", "jpeg", "h264", "tts"):
            (self.upload_dir / name).mkdir(parents=True, exist_ok=True)

    def parse_filename(self, filename: str) -> tuple[str, str]:
        safe = Path(filename).name
        match = FILE_RE.match(safe)
        if not match:
            raise ValueError("文件名格式不正确，应为 设备序列号-时间戳.pcm/mp3/jpeg/h264")
        ext = match.group("ext").lower()
        if ext == "jpg":
            ext = "jpeg"
        return match.group("sn"), ext

    def max_size_bytes(self, file_type: str) -> int:
        sizes = {
            "pcm": settings.max_pcm_size_mb,
            "mp3": settings.max_pcm_size_mb,
            "jpeg": settings.max_jpeg_size_mb,
            "h264": settings.max_h264_size_mb,
        }
        return sizes.get(file_type, 10) * 1024 * 1024

    async def save_upload(self, upload: UploadFile) -> StoredFile:
        filename = Path(upload.filename or "").name
        sn, file_type = self.parse_filename(filename)
        target = self.upload_dir / file_type / filename
        if target.exists():
            raise ValueError(f"文件已存在，拒绝覆盖：{filename}")

        max_size = self.max_size_bytes(file_type)
        size = 0
        with target.open("wb") as out:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_size:
                    target.unlink(missing_ok=True)
                    raise ValueError(f"上传文件过大，{file_type} 文件最大允许 {max_size // 1024 // 1024}MB")
                out.write(chunk)
        return StoredFile(sn=sn, filename=filename, file_type=file_type, path=target, size=size)

    def find_uploaded(self, filename: str) -> Path | None:
        safe = Path(filename).name
        try:
            _, file_type = self.parse_filename(safe)
        except ValueError:
            return None
        path = self.upload_dir / file_type / safe
        return path if path.exists() else None

    def file_response_path(self, filename: str) -> Path:
        path = self.find_uploaded(filename)
        if path is None:
            tts_path = self.upload_dir / "tts" / Path(filename).name
            if tts_path.exists():
                return tts_path
            raise FileNotFoundError(filename)
        return path


storage_service = StorageService()
