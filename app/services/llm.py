from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any, Callable

import httpx

from app.config import settings
from app.services.logger import logger
from app.services.schemas import result_guard


HAZARD_SYSTEM_PROMPT = (
    "你是建筑施工安全检查助手。请根据现场图片或描述，先识别施工安全隐患，最后给出整改方案。"
    "必须按以下结构输出：\n"
    "一、安全隐患分析\n"
    "逐条列出隐患。每条格式为：【隐患类别】（风险等级：高/中/低）具体描述、可能风险。"
    "如果未发现明显隐患，请说明「未发现明显隐患」，并说明图片中哪些关键信息不足。\n\n"
    "二、整改方案\n"
    "针对每条隐患给出可执行整改措施，包括立即处置、规范整改、复查验收要点。"
    "如果未发现明显隐患，则给出复核建议、补拍建议和继续人工检查要点。"
    "不要输出 JSON、Markdown 代码块或表格。"
    "所有判断仅供参考，必须由现场人员人工复核。"
)


def _tts_friendly(text: str) -> str:
    """把分条目的可读文本清理成适合语音播报的句子。"""
    cleaned = text.replace("【", "").replace("】", "：")
    cleaned = re.sub(r"\n{2,}", "。", cleaned)
    cleaned = cleaned.replace("\n", "，")
    return cleaned.strip()


def _ensure_rectification_section(text: str) -> str:
    """模型偶尔会只输出隐患描述，这里兜底补齐整改段落。"""
    clean = text.strip()
    if not clean or "整改方案" in clean or "整改建议" in clean:
        return clean
    if "未发现明显隐患" in clean:
        return (
            f"{clean}\n\n"
            "二、整改方案\n"
            "1. 由现场人员结合实物和作业环境进行人工复核，确认图片未覆盖区域是否存在隐患。\n"
            "2. 如图片模糊、角度过近或关键设备不完整，请重新拍摄包含作业人员、设备全貌、线缆走向和周边环境的清晰图片。\n"
            "3. 继续按现场安全检查流程核对防护用品、设备状态、临边洞口、用电线路和消防通道等要点。"
        )
    return (
        f"{clean}\n\n"
        "二、整改方案\n"
        "1. 立即停止相关危险作业，设置警戒或隔离措施，避免人员继续暴露在风险区域。\n"
        "2. 按现场安全规范对隐患部位进行整改，重点复核设备状态、防护措施、线路敷设、材料堆放和作业环境。\n"
        "3. 整改完成后由现场负责人复查验收，确认隐患消除后再恢复作业，并保留整改记录。"
    )


class LLMService:
    async def chat_text(self, text: str) -> str:
        if settings.llm_provider == "openai_compatible" and settings.llm_base_url:
            return await self._openai_chat_text(text)
        return self._mock_chat_text(text)

    async def analyze_text(self, text: str, context: dict[str, Any]) -> dict[str, Any]:
        if settings.llm_provider == "openai_compatible" and settings.llm_base_url:
            return await self._openai_chat(text)
        return result_guard.normalize_hazard_result(self._mock_text(text, context), add_defaults=not settings.use_dynamic_reply)

    async def analyze_multimodal(self, text: str, image_path: str | None, context: dict[str, Any], on_chunk: Callable[[str], Any] | None = None) -> dict[str, Any]:
        if settings.llm_provider == "openai_compatible" and settings.llm_base_url:
            return await self._openai_chat(text, on_chunk=on_chunk, image_path=image_path)
        return result_guard.normalize_hazard_result(self._mock_image(text, image_path, context), add_defaults=not settings.use_dynamic_reply)

    @staticmethod
    def _image_data_url(image_path: str) -> str:
        """把本地图片读成 base64 data URL。网关访问不到本机文件地址，必须内联传输。"""
        raw = Path(image_path).read_bytes()
        b64 = base64.b64encode(raw).decode("ascii")
        return f"data:image/jpeg;base64,{b64}"

    async def _openai_chat(
        self,
        text: str,
        on_chunk: Callable[[str], Any] | None = None,
        image_path: str | None = None,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {settings.llm_api_key or 'EMPTY'}"}

        # 多模态：图片以 base64 内联，模型才真正"看到"现场
        if image_path:
            user_content: Any = [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": self._image_data_url(image_path)}},
            ]
        else:
            user_content = text

        payload = {
            "model": settings.llm_model,
            "messages": [
                {"role": "system", "content": HAZARD_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.2,
            "stream": True,
        }
        logger.info("[LLM请求] 模型=%s, 提示=%s, 带图=%s", settings.llm_model, text, bool(image_path))

        # 流式接收：每个 token 直接透传给 on_chunk（打字机效果）
        content = ""
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream("POST", f"{settings.llm_base_url.rstrip('/')}/chat/completions", headers=headers, json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line or line == "data: [DONE]":
                        continue
                    if not line.startswith("data: "):
                        continue
                    try:
                        chunk_json = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue
                    delta = chunk_json.get("choices", [{}])[0].get("delta", {})
                    chunk_content = delta.get("content", "")
                    if chunk_content:
                        content += chunk_content
                        if on_chunk:
                            await on_chunk(chunk_content)

        logger.info("[LLM响应] 输出=%s", content[:500] if len(content) > 500 else content)

        # 模型直接输出可读文本，不再解析 JSON。整段文本作为 web_summary，
        # 语音播报用清理后的版本。need_manual_review 固定 true 满足安全守卫。
        full_text = _ensure_rectification_section(content)
        risk_found = bool(full_text) and "未发现明显隐患" not in full_text
        return {
            "intent": "hazard_check",
            "risk_found": risk_found,
            "risk_items": [],
            "rectification": [],
            "need_manual_review": True,
            "web_summary": full_text,
            "tts_reply": _tts_friendly(full_text)[:120],
        }

    async def _openai_chat_text(self, text: str) -> str:
        headers = {"Authorization": f"Bearer {settings.llm_api_key or 'EMPTY'}"}
        payload = {
            "model": settings.llm_model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是智能头盔施工安全助手。回答要简洁、可直接语音播报，避免 Markdown 表格。",
                },
                {"role": "user", "content": text},
            ],
            "temperature": 0.2,
        }
        logger.info("[LLM请求] 模型=%s, 输入=%s", settings.llm_model, json.dumps(payload["messages"], ensure_ascii=False))
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(f"{settings.llm_base_url.rstrip('/')}/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
        logger.info("[LLM响应] 输出=%s", content[:500] if len(content) > 500 else content)
        return content

    def _mock_chat_text(self, text: str) -> str:
        return (
            f"已收到管理端输入：{text.strip()}。"
            "请现场人员先确认作业区域安全，按检查流程逐项核对，发现异常立即停工并上报。"
        )

    def _mock_text(self, text: str, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "intent": "ask_hazard",
            "risk_found": False,
            "risk_items": [],
            "rectification": [],
            "need_manual_review": False,
            "web_summary": "请上传现场图片，我将协助分析是否存在施工隐患。",
            "tts_reply": "请上传现场图片，我将协助分析是否存在施工隐患。",
        }

    def _mock_image(self, text: str, image_path: str | None, context: dict[str, Any]) -> dict[str, Any]:
        image_name = Path(image_path).name if image_path else ""
        logger.info("[模型分析] mock 图片隐患分析完成：%s", image_name or "无图片")
        if "no_hazard" in image_name.lower():
            return {
                "intent": "hazard_check",
                "valid_scene": True,
                "image_quality": "normal",
                "risk_found": False,
                "risk_items": [],
                "need_manual_review": True,
                "web_summary": (
                    "一、安全隐患分析\n"
                    "未发现明显隐患。当前图片未呈现明确违规作业或设备异常，但仍需现场人员结合未覆盖区域人工复核。\n\n"
                    "二、整改方案\n"
                    "1. 继续按检查流程核对作业人员防护、设备状态、线路走向和现场环境。\n"
                    "2. 如图片未覆盖关键部位，请补拍清晰全景和关键细节。"
                ),
                "tts_reply": "暂未发现明显隐患，请按检查流程继续人工复核。",
            }
        return {
            "intent": "hazard_check",
            "valid_scene": True,
            "image_quality": "normal",
            "risk_found": True,
            "risk_level": "medium",
            "risk_items": [
                {
                    "name": "邻近线路间距离不符合规范要求",
                    "evidence": "现场图片中线路排列较密集，需人工复核。",
                    "rule": "线路与邻近线路或固定物的距离应符合规范要求。",
                    "confidence": 0.72,
                }
            ],
            "rectification": ["调整线路间距，按规范重新敷设。", "复查线路绝缘、固定和短路过载保护。"],
            "need_manual_review": True,
            "web_summary": (
                "一、安全隐患分析\n"
                "【临时用电】（风险等级：中）现场图片中线路排列较密集，疑似邻近线路间距离不符合规范要求，需人工复核。"
                "该问题可能导致线路发热、短路或触电风险。\n\n"
                "二、整改方案\n"
                "1. 立即停电检查相关回路，确认线缆绝缘、接头紧固和保护装置状态。\n"
                "2. 调整线路间距，按规范重新敷设并固定线缆，避免交叉缠绕和贴靠。\n"
                "3. 整改后复查线路绝缘、短路过载保护和箱内标识，验收合格后再恢复使用。"
            ),
            "tts_reply": "发现邻近线路间距离不符合规范要求，请依据《施工现场临时用电安全技术规范》进行整改，具体整改方案我已经推送给您。",
        }


llm_service = LLMService()
