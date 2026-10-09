from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse

from app.services.broadcaster import broadcaster
from app.services.errors import ErrorCode
from app.services.logger import logger
from app.services.storage import storage_service

router = APIRouter()


@router.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    logger.info("[文件上传] 收到上传请求，原始文件名：%s", file.filename)
    try:
        stored = await storage_service.save_upload(file)
    except ValueError as exc:
        message = str(exc)
        if "过大" in message:
            code = ErrorCode.FILE_TOO_LARGE
        elif "格式" in message or "后缀" in message:
            code = ErrorCode.UNSUPPORTED_FILE_TYPE
        else:
            code = "UPLOAD_REJECTED"
        logger.warning("[文件上传] 上传被拒绝：%s", message)
        return JSONResponse(status_code=400, content={"ok": False, "error_code": code, "error": message})
    logger.info("[文件上传] 收到设备 %s 上传文件 %s，类型 %s，大小 %s 字节", stored.sn, stored.filename, stored.file_type, stored.size)
    await broadcaster.publish(stored.sn, {"type": "file_uploaded", "file_type": stored.file_type, "file": stored.filename, "size": stored.size}, persist=False)
    return {
        "code": 0,
        "msg": "success",
        "data": {
            "sn": stored.sn,
            "file": stored.filename,
            "type": stored.file_type,
            "size": stored.size,
        },
    }


@router.get("/api/files/{filename}")
async def get_file(filename: str):
    try:
        path = storage_service.file_response_path(filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="文件不存在") from exc
    return FileResponse(path)
