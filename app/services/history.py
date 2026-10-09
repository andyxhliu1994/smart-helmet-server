from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from app.config import settings

CN_TZ = timezone(timedelta(hours=8))


def now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


class HistoryService:
    def __init__(self) -> None:
        self.base = settings.data_dir / "conversations"
        self.events = settings.data_dir / "events"
        self.base.mkdir(parents=True, exist_ok=True)
        self.events.mkdir(parents=True, exist_ok=True)

    def session_id(self, sn: str) -> str:
        return f"{sn}-{datetime.now(CN_TZ).strftime('%Y%m%d-%H%M%S')}"

    def append(self, sn: str, event: dict[str, Any]) -> dict[str, Any]:
        record = {"ts": now_iso(), "sn": sn, **event}
        path = self.base / f"{sn}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def devices(self) -> list[str]:
        return sorted(path.stem for path in self.base.glob("*.jsonl"))

    def records(self, sn: str | None = None) -> list[dict[str, Any]]:
        files = [self.base / f"{sn}.jsonl"] if sn else list(self.base.glob("*.jsonl"))
        rows: list[dict[str, Any]] = []
        for path in files:
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rows.append(json.loads(line))
        return rows

    def sessions(self, sn: str | None = None, keyword: str | None = None) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in self.records(sn):
            if keyword and keyword not in json.dumps(row, ensure_ascii=False):
                continue
            sid = row.get("session_id") or f"{row.get('sn')}-default"
            grouped.setdefault(sid, []).append(row)
        result = []
        for sid, rows in grouped.items():
            rows.sort(key=lambda x: x.get("ts", ""))
            text = next((r.get("text") or r.get("message") or r.get("title") for r in rows if r.get("text") or r.get("message") or r.get("title")), "")
            result.append({"session_id": sid, "sn": rows[0].get("sn"), "start": rows[0].get("ts"), "end": rows[-1].get("ts"), "count": len(rows), "summary": text})
        return sorted(result, key=lambda x: x["start"] or "", reverse=True)

    def session_detail(self, session_id: str) -> list[dict[str, Any]]:
        return [r for r in self.records() if r.get("session_id") == session_id]


history_service = HistoryService()
