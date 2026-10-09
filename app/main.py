from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.routes import admin, history, knowledge, safe_tts, upload, ws_device, ws_web
from app.services.broadcaster import broadcaster

app = FastAPI(title="智能头盔隐患识别 Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload.router)
app.include_router(ws_device.router)
app.include_router(ws_web.router)
app.include_router(knowledge.router)
app.include_router(history.router)
app.include_router(admin.router)
app.include_router(safe_tts.router)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await broadcaster.notify_server_shutdown()


@app.get("/")
async def index():
    return FileResponse("app/static/index.html")


@app.get("/client/{sn}")
async def client_dialog(sn: str):
    return FileResponse("app/static/index.html")


@app.get("/knowledge")
async def knowledge_page():
    return FileResponse("app/static/knowledge.html")


@app.get("/history")
async def history_page():
    return FileResponse("app/static/history.html")


@app.get("/admin")
async def admin_page():
    return FileResponse("app/static/admin.html")


@app.get("/test-client")
async def test_client_page():
    return FileResponse("app/static/test_client.html")


@app.get("/api/health")
async def health():
    return {"ok": True}


@app.get("/api/status")
async def status():
    devices = {}
    for sn, session in broadcaster.sessions.items():
        devices[sn] = {
            "cmd": "connected" if session.cmd_ws else "disconnected",
            "voice": "connected" if session.voice_ws else "disconnected",
            "web_clients": len(session.web_clients),
            "current_task": session.current_task,
            "task_state": session.task_state,
        }
    return {"asr": "ready", "llm": "ready", "tts": "ready", "devices": devices}


@app.get("/api/web-config")
async def web_config():
    return {"web_nav_visible": settings.web_nav_visible, "default_sn": settings.default_sn}
