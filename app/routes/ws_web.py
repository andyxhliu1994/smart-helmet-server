from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.broadcaster import broadcaster

router = APIRouter()


@router.websocket("/ws/web/{sn}")
async def web_socket(sn: str, websocket: WebSocket):
    await websocket.accept()
    replay = websocket.query_params.get("replay", "1").lower() not in {"0", "false", "no", "off"}
    await broadcaster.register_web(sn, websocket, replay=replay)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        broadcaster.unregister_web(sn, websocket)
