from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.agent import agent_service
from app.services.broadcaster import broadcaster
from app.services.logger import logger

router = APIRouter()


def _log_task_result(task: asyncio.Task) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        logger.warning("[设备消息] 后台处理 cmd 消息被取消")
        return
    except Exception as exc:
        logger.error("[设备消息] 后台处理 cmd 消息失败：%s", exc)


@router.websocket("/{sn}/cmd")
async def device_cmd(sn: str, websocket: WebSocket):
    await websocket.accept()
    await broadcaster.register_cmd(sn, websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            payload = json.loads(raw)
            logger.info("[设备消息] 收到设备 %s cmd 消息：%s", sn, raw)
            task = asyncio.create_task(agent_service.handle_cmd(sn, payload))
            task.add_done_callback(_log_task_result)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("[设备断开] 设备 %s cmd 通道异常断开：%s", sn, exc)
    finally:
        await broadcaster.unregister_cmd(sn, websocket)


@router.websocket("/{sn}/voice")
async def device_voice(sn: str, websocket: WebSocket):
    await websocket.accept()
    await broadcaster.register_voice(sn, websocket)
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("[设备断开] 设备 %s voice 通道异常断开：%s", sn, exc)
    finally:
        await broadcaster.unregister_voice(sn, websocket)
