from __future__ import annotations

from typing import Any

from app.services.knowledge import knowledge_service


class IntentService:
    async def classify(self, text: str, context: dict[str, Any]) -> dict[str, Any]:
        clean = text.replace(" ", "")
        flow = knowledge_service.match_check_flow(clean)
        if any(word in clean for word in ("再说一遍", "重复", "没听清", "刚才")):
            return {"intent": "repeat", "confidence": 0.9, "slots": {}}
        if any(word in clean for word in ("结束", "不用了", "取消", "停止检查")):
            return {"intent": "cancel", "confidence": 0.86, "slots": {}}
        if any(word in clean for word in ("收到", "整改", "谢谢", "明白", "马上改", "立即整改")):
            return {"intent": "confirm_rectify", "confidence": 0.9, "slots": {}}
        if any(word in clean for word in ("流程", "怎么查", "怎么检查", "检查步骤", "告诉我")):
            return {"intent": "ask_flow", "confidence": 0.88, "slots": {"object": self._flow_object(flow) or self._task_name(clean)}}
        if any(word in clean for word in ("有没有隐患", "有没有问题", "看看", "分析", "检查一下", "是否存在")):
            return {"intent": "ask_hazard", "confidence": 0.84, "slots": {}}
        if flow:
            return {"intent": "ask_flow", "confidence": 0.82, "slots": {"object": self._flow_object(flow)}}
        if any(word in clean for word in ("开始检查", "开始查")):
            return {"intent": "start_check", "confidence": 0.82, "slots": {"object": self._task_name(clean)}}
        if "检查" in clean and self._task_name(clean):
            return {"intent": "start_check", "confidence": 0.82, "slots": {"object": self._task_name(clean)}}
        return {"intent": "unknown", "confidence": 0.4, "slots": {}}

    def _task_name(self, text: str) -> str | None:
        if "三级配电箱" in text or "配电箱" in text:
            return "三级配电箱线路检查"
        return None

    def _flow_object(self, flow: dict[str, Any] | None) -> str | None:
        if not flow:
            return None
        return flow.get("object_name") or flow.get("name")


intent_service = IntentService()
