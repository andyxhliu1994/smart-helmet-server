from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn


ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

out = open(ROOT / "uvicorn.out.log", "a", encoding="utf-8", buffering=1)
err = open(ROOT / "uvicorn.err.log", "a", encoding="utf-8", buffering=1)
sys.stdout = out
sys.stderr = err

print("[启动] 主服务启动中：http://0.0.0.0:18787", flush=True)
uvicorn.run("app.main:app", host="0.0.0.0", port=18787, log_level="info")
