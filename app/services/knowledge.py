from __future__ import annotations

import json
import re
import uuid
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from app.services.history import now_iso


class KnowledgeService:
    def __init__(self) -> None:
        self.path = Path("app/knowledge/check_flows.json")
        self.docx_dir = Path("docx")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("[]", encoding="utf-8")
        self.flows: list[dict[str, Any]] = []
        self.docx_flows: list[dict[str, Any]] = []
        self.reload()

    def reload(self) -> list[dict[str, Any]]:
        self.flows = json.loads(self.path.read_text(encoding="utf-8") or "[]")
        self.docx_flows = self._load_docx_flows()
        return self.flows

    def save(self) -> None:
        self.path.write_text(json.dumps(self.flows, ensure_ascii=False, indent=2), encoding="utf-8")

    def list_flows(self) -> list[dict[str, Any]]:
        return [*self.docx_flows, *self.flows]

    def get_flow(self, flow_id: str) -> dict[str, Any] | None:
        return next((flow for flow in self.flows if flow.get("id") == flow_id), None)

    def create_flow(self, payload: dict[str, Any]) -> dict[str, Any]:
        flow = {
            "id": payload.get("id") or str(uuid.uuid4()),
            "name": payload.get("name", ""),
            "enabled": bool(payload.get("enabled", True)),
            "keywords": payload.get("keywords", []),
            "flow": payload.get("flow", []),
            "tts_reply": payload.get("tts_reply", ""),
            "updated_at": now_iso(),
        }
        self._validate(flow)
        self.flows.append(flow)
        self.save()
        return flow

    def update_flow(self, flow_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        flow = self.get_flow(flow_id)
        if flow is None:
            raise KeyError(flow_id)
        for key in ("name", "enabled", "keywords", "flow", "tts_reply"):
            if key in payload:
                flow[key] = payload[key]
        flow["updated_at"] = now_iso()
        self._validate(flow)
        self.save()
        return flow

    def delete_flow(self, flow_id: str) -> None:
        before = len(self.flows)
        self.flows = [flow for flow in self.flows if flow.get("id") != flow_id]
        if len(self.flows) == before:
            raise KeyError(flow_id)
        self.save()

    def match_check_flow(self, text: str) -> dict[str, Any] | None:
        normalized = self._query_key(text)
        if not normalized:
            return None
        docx_match = self._best_flow_match(normalized, self.docx_flows)
        if docx_match:
            return docx_match
        return self._best_flow_match(normalized, self.flows)

    def _best_flow_match(self, normalized: str, flows: list[dict[str, Any]]) -> dict[str, Any] | None:
        best: tuple[int, dict[str, Any] | None] = (0, None)
        for flow in flows:
            if not flow.get("enabled", True):
                continue
            score = 0
            for keyword in flow.get("keywords", []):
                keyword = self._normalize(str(keyword))
                if not keyword:
                    continue
                if keyword == normalized:
                    score += 6
                elif normalized in keyword or keyword in normalized:
                    score += 3
            name = self._normalize(str(flow.get("name") or ""))
            if name and (name == normalized or normalized in name or name in normalized):
                score += 3
            if score > best[0]:
                best = (score, flow)
        return best[1] if best[0] >= 2 else None

    def _load_docx_flows(self) -> list[dict[str, Any]]:
        if not self.docx_dir.exists():
            return []

        flows: list[dict[str, Any]] = []
        for path in sorted(self.docx_dir.glob("*.docx")):
            try:
                paragraphs = self._extract_docx_paragraphs(path)
            except (KeyError, OSError, zipfile.BadZipFile, ElementTree.ParseError):
                continue
            flows.extend(self._section_flows_from_paragraphs(path, paragraphs))
        return flows

    def _extract_docx_paragraphs(self, path: Path) -> list[str]:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(xml)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

        paragraphs: list[str] = []
        for paragraph in root.findall(".//w:p", ns):
            text = "".join(node.text or "" for node in paragraph.findall(".//w:t", ns)).strip()
            if text:
                paragraphs.append(text)
        return paragraphs

    def _section_flows_from_paragraphs(self, path: Path, paragraphs: list[str]) -> list[dict[str, Any]]:
        flows: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None

        for paragraph in paragraphs:
            heading = self._parse_check_heading(paragraph)
            if heading:
                if current and current["flow"]:
                    flows.append(current)
                number, subject, score_text = heading
                current = {
                    "id": f"docx_{path.stem}_{number}_{self._normalize(subject)}",
                    "name": f"{subject}检查内容及评分标准",
                    "object_name": subject,
                    "section_no": number,
                    "score_text": score_text,
                    "enabled": True,
                    "keywords": self._keywords_for_subject(subject),
                    "flow": [],
                    "tts_reply": "",
                    "source_file": path.name,
                    "source": "docx_section",
                }
                continue

            if current and self._is_scoring_item(paragraph):
                current["flow"].append(paragraph)

        if current and current["flow"]:
            flows.append(current)

        for flow in flows:
            count = len(flow["flow"])
            flow["tts_reply"] = f"{flow['name']}有{count}项内容，请按照内容逐项检查。"
        return flows

    def _parse_check_heading(self, text: str) -> tuple[str, str, str] | None:
        match = re.match(r"^(\d+(?:\.\d+)?\.?)\s+(.+?)检查内容及评分标准(?:（([^）]+)）)?$", text)
        if not match:
            return None
        number, subject, score_text = match.groups()
        number = number.rstrip(".")
        return number, subject.strip(), score_text or ""

    def _is_scoring_item(self, text: str) -> bool:
        return bool(re.match(r"^\d+\.\s*", text)) and "扣" in text

    def _keywords_for_subject(self, subject: str) -> list[str]:
        candidates = [subject]
        candidates.extend(part for part in re.split(r"[与和及、/／]", subject) if part)
        if "配电箱" in subject:
            candidates.extend(["三级配电箱", "三级配电箱线路", "配电箱"])
        if "开关箱" in subject:
            candidates.append("开关箱")

        keywords: list[str] = []
        for candidate in candidates:
            candidate = candidate.strip()
            if candidate and candidate not in keywords:
                keywords.append(candidate)
        return keywords

    def _normalize(self, text: str) -> str:
        return re.sub(r"[\s，。！？；：、,.!?;:（）()《》<>]+", "", text)

    def _query_key(self, text: str) -> str:
        normalized = self._normalize(text)
        for word in ("请", "帮我", "帮忙", "告诉我", "怎么", "如何", "一下"):
            normalized = normalized.replace(word, "")
        for word in ("检查内容及评分标准", "检查内容", "评分标准", "检查项", "检查流程", "检查步骤", "流程", "内容", "标准", "检查", "查看", "查询"):
            normalized = normalized.replace(word, "")
        return normalized

    def import_docx_draft(self, text: str) -> dict[str, Any]:
        lines = [line.strip() for line in re.split(r"[\r\n。；;]", text) if line.strip()]
        items = [line + "。" for line in lines if any(k in line for k in ("线路", "电缆", "配电箱", "保护", "绝缘"))][:10]
        return {
            "id": str(uuid.uuid4()),
            "name": "导入的检查流程",
            "enabled": False,
            "keywords": ["检查流程"],
            "flow": items,
            "tts_reply": "已找到检查流程，请按页面内容检查。",
            "updated_at": now_iso(),
        }

    def _validate(self, flow: dict[str, Any]) -> None:
        if not flow.get("name"):
            raise ValueError("流程名称不能为空")
        if not flow.get("keywords"):
            raise ValueError("关键词不能为空")
        if not flow.get("flow"):
            raise ValueError("检查流程条目不能为空")


knowledge_service = KnowledgeService()
