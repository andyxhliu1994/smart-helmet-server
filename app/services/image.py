from __future__ import annotations

from pathlib import Path


class ImageService:
    def validate_basic(self, path: Path) -> dict:
        data = path.read_bytes()
        if len(data) < 100:
            return {
                "valid": False,
                "error_code": "IMAGE_POOR_QUALITY",
                "message": "图片文件过小或损坏，无法分析。",
                "tts_reply": "图片不清晰，无法判断隐患，请重新拍摄配电箱内部线路。",
            }
        lower = path.name.lower()
        marker_text = data.decode("latin1", errors="ignore")
        if "NON_SITE_MARKER" in marker_text or any(key in lower for key in ("non_site", "nonsite", "invalid_scene")):
            return {
                "valid": False,
                "error_code": "IMAGE_INVALID_SCENE",
                "message": "图片内容与当前检查任务不匹配，未进行隐患判断。",
                "tts_reply": "当前图片不是三级配电箱线路现场，请重新拍摄检查对象。",
            }
        if "BLURRY_MARKER" in marker_text or any(key in lower for key in ("blurry", "blur", "poor")):
            return {
                "valid": False,
                "error_code": "IMAGE_POOR_QUALITY",
                "message": "图片质量不足，无法可靠判断是否存在隐患。",
                "tts_reply": "图片不清晰，无法判断隐患，请重新拍摄配电箱内部线路。",
            }
        return {"valid": True}


image_service = ImageService()
