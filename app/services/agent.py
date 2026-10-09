from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.audio import AudioConversionError, audio_service
from app.services.asr import asr_service
from app.services.broadcaster import broadcaster
from app.services.errors import ErrorCode
from app.services.image import image_service
from app.services.intent import intent_service
from app.services.knowledge import knowledge_service
from app.services.llm import llm_service
from app.services.logger import logger
from app.services.search import search_service
from app.services.storage import storage_service
from app.services.tts import tts_service


PHOTO_PROMPT = "请前往需检查的三级配电箱线路现场，并拍摄图像上传"
IMAGE_RECEIVED_REPLY = "收到现场图像，已记录影音素材。现在协助分析是否存在隐患。"
HAZARD_SUMMARY_REPLY = "发现邻近线路间距离不符合规范要求，请依据《施工现场临时用电安全技术规范》进行整改，具体整改方案我已经推送给您。"
FOLLOW_UP_PROMPT = "如果有细节或其他作业内容需要检查分析，请您随时告诉我"
RECTIFICATION_GUIDE = """一、隐患问题
邻近线路间距离不符合规范要求
二、隐患风险分析
线路邻近距离不足，当导线绝缘老化、受潮或箱体震动时，极易发生相间、相对地短路，产生高温电弧，引发电气火灾或设备烧毁；若相线与 PE 线距离过近，一旦绝缘击穿，会导致箱体、设备外壳带电，直接引发人员触电事故；线路混走、距离不足，一处线路故障易波及邻近回路，导致多台设备同时断电或损坏，影响施工安全与进度。
三、整改建议（依据 J G J / T 46 - 2024《施工现场临时用电安全技术规范》）
线路重新排布：拆除箱内所有不规范导线，按相序分色分侧布置（黄 / 绿 / 红为相线、蓝色为零线、黄绿双色为 PE 线），不同相序、回路导线分区走线路径；确保相线间、相线与零线 / PE 线间电气间隙≥ 12m m ，控制回路导线电气间隙≥ 4m m；箱内加装线槽或线卡，将导线整齐固定，禁止交叉、缠绕、贴靠。
导线与端子整改：更换绝缘层破损、老化的导线，确保所有导线绝缘完好；接线端子采用冷压端子压接，一孔一线，禁止多线共接，端子间预留足够安全距离；动力回路与照明回路分路敷设，必要时加装绝缘隔板隔离，保证间距符合要求。
防护与验收：箱内加装绝缘护套或绝缘套管，对邻近易触碰的导线进行二次绝缘防护；整改完成后，用万用表检测相线间、相线与 PE 线间绝缘电阻≥ 0. 5M Ω；对所有回路进行通电试机，检查无打火、发热现象，张贴回路标识与安全警示。
补充执行要点
整改过程中必须断电作业，并悬挂 “禁止合闸，有人工作” 标识牌；所有导线长度应预留检修余量，避免过紧拉扯导致端子移位，破坏安全距离；定期（每周）对箱内线路进行巡检，重点检查导线间距、绝缘层状态，发现问题立即停机整改。"""
STREAM_CHARS = 6
STREAM_DELAY_SECONDS = 0.08
LLM_TTS_MAX_CHARS = 80
LLM_TTS_MIN_CHARS = 24
LLM_TTS_ENDINGS = set("。！？!?；;")
LLM_TTS_SOFT_BREAKS = set("，,、：:")


class AgentService:
    async def handle_cmd(self, sn: str, payload: dict[str, Any]) -> None:
        cmd = payload.get("cmd")
        file = payload.get("file")
        msgid = payload.get("msgid")
        session = broadcaster.get(sn)
        event_key = f"{sn}:{msgid}:{cmd}:{file}"

        if event_key in session.processed_events:
            await broadcaster.send_cmd(sn, {
                "version": "1.0",
                "msgid": msgid,
                "cmd": "ack",
                "msg": "duplicate_ignored",
                "error_code": ErrorCode.DUPLICATE_EVENT,
            })
            await broadcaster.publish(sn, {
                "type": "duplicate_ignored",
                "error_code": ErrorCode.DUPLICATE_EVENT,
                "message": "重复上传事件已忽略。",
            })
            return
        session.processed_events.add(event_key)

        if cmd == "demo_step":
            await broadcaster.publish(sn, {"type": "demo_step", **payload}, persist=False)
            return
        if cmd == "demo_end":
            session.task_state = "closed"
            await broadcaster.publish(sn, {"type": "demo_end", "msg": payload.get("msg", "OVER")})
            return
        if cmd == "text_input":
            text = str(payload.get("text") or "").strip()
            if not text:
                await broadcaster.send_cmd(sn, {
                    "version": "1.0",
                    "msgid": msgid,
                    "cmd": "ack",
                    "msg": "empty_text",
                    "error_code": ErrorCode.ASR_EMPTY_TEXT,
                })
                await broadcaster.publish(sn, {
                    "type": "system_warning",
                    "message": "文字内容为空，请输入要检查的目标或问题。",
                })
                return

            await broadcaster.send_cmd(sn, {"version": "1.0", "msgid": msgid, "cmd": "ack", "msg": "received"})
            await self.handle_text(sn, text, f"user-text-{msgid or 'web'}")
            return
        if not file:
            return

        actual_path = storage_service.find_uploaded(file)
        if actual_path is None:
            await broadcaster.send_cmd(sn, {
                "version": "1.0",
                "msgid": msgid,
                "cmd": "ack",
                "file": file,
                "msg": "file_not_found",
                "error_code": ErrorCode.FILE_NOT_FOUND,
            })
            await broadcaster.publish(sn, {
                "type": "system_error",
                "error_code": ErrorCode.FILE_NOT_FOUND,
                "message": f"上传通知已收到，但未找到文件：{file}",
            })
            logger.error("[异常] 上传通知已收到，但未找到文件：%s", file)
            return

        await broadcaster.send_cmd(sn, {"version": "1.0", "msgid": msgid, "cmd": "ack", "file": file, "msg": "received"})
        suffix = actual_path.suffix.lower().lstrip(".")
        if suffix == "jpg":
            suffix = "jpeg"
        logger.info("[文件上传] 上传通知已确认，准备按文件类型处理：cmd=%s file=%s suffix=%s", cmd, file, suffix)
        expected = {"upload_pcm": "pcm", "upload_mp3": "mp3", "upload_jpeg": "jpeg", "upload_h264": "h264"}.get(cmd)
        if expected and expected != suffix:
            logger.warning("[文件上传] cmd 与文件后缀不一致：cmd=%s file=%s", cmd, file)
            await broadcaster.publish(sn, {"type": "system_warning", "message": "上传事件类型与文件后缀不一致，服务端已按文件后缀处理。"})

        if suffix == "pcm":
            await self.handle_pcm(sn, actual_path)
        elif suffix == "mp3":
            try:
                pcm_path = audio_service.mp3_file_to_pcm_file(actual_path)
            except AudioConversionError as exc:
                logger.error("[语音识别] MP3 转 PCM 失败：%s", exc)
                await self._speak_error(sn, ErrorCode.ASR_FAILED, "MP3 音频转换失败，请重新上传 PCM 或 WAV 音频。")
                return
            await broadcaster.publish(sn, {
                "type": "audio_converted",
                "source_file": file,
                "file": pcm_path.name,
                "message": "MP3 已转换为 PCM，准备进行语音识别。",
            }, persist=False)
            await self.handle_pcm(sn, pcm_path)
        elif suffix == "jpeg":
            await self.handle_jpeg(sn, actual_path)
        elif suffix == "h264":
            await broadcaster.publish(sn, {"type": "upload_h264", "file": file, "message": "视频已保存，MVP 暂不分析。"})

    async def handle_pcm(self, sn: str, path: Path) -> None:
        try:
            await broadcaster.cancel_tts(sn)
        except BaseException as exc:
            logger.error("[语音下发] 中断上一段 TTS 时出现异常，继续处理新 PCM：%s", exc)
        logger.info("[语音识别] 准备处理新的 PCM：%s", path.name)
        try:
            text = await asr_service.transcribe_pcm(str(path))
        except Exception as exc:
            logger.error("[语音识别] ASR 失败：%s", exc)
            await self._speak_error(sn, ErrorCode.ASR_FAILED, "语音识别失败，请重新说一遍。")
            return

        if not asr_service.is_valid_text(text):
            await broadcaster.publish(sn, {
                "type": "asr_failed",
                "error_code": ErrorCode.ASR_EMPTY_TEXT,
                "file": path.name,
                "message": "未识别到有效语音，已提示设备端重新录音。",
            })
            pcm = await tts_service.synthesize_pcm(settings.asr_failed_reply)
            await broadcaster.send_voice(sn, pcm, settings.asr_failed_reply)
            return

        await self._handle_user_text(sn, text, f"user-{path.stem}", file=path.name)

    async def handle_text(self, sn: str, text: str, message_id: str) -> None:
        try:
            await broadcaster.cancel_tts(sn, "new_text_received")
        except BaseException as exc:
            logger.error("[语音下发] 中断上一段 TTS 时出现异常，继续处理新文字：%s", exc)
        logger.info("[文字输入] 收到设备 %s 文字输入：%s", sn, text)
        await self._handle_user_text(sn, text, message_id, source="text")

    async def _handle_user_text(self, sn: str, text: str, message_id: str, file: str | None = None, source: str = "asr") -> None:
        session = broadcaster.get(sn)
        session.last_user_input = text
        event: dict[str, Any] = {"type": "asr_result", "message_id": message_id, "text": text, "source": source}
        if file:
            event["file"] = file
        await broadcaster.publish(sn, event)
        intent = await intent_service.classify(text, self._context(sn))
        flow = knowledge_service.match_check_flow(text)
        if flow and intent["intent"] not in {"repeat", "cancel", "confirm_rectify"}:
            intent = {
                "intent": "ask_flow",
                "confidence": max(float(intent.get("confidence", 0)), 0.88),
                "slots": {"object": flow.get("object_name") or flow.get("name")},
            }
        await broadcaster.publish(sn, {"type": "intent_result", "intent": intent["intent"], "confidence": intent["confidence"], "slots": intent.get("slots", {})}, persist=False)

        match intent["intent"]:
            case "repeat":
                await self._reply_thought(sn, "识别到设备端要求重复上一条回复，准备调取最近一次 agent 答复。", "thought-repeat")
                await self._reply_simple(sn, session.last_agent_reply or "当前没有可以重复播报的内容。")
            case "cancel":
                session.task_state = "closed"
                await self._reply_thought(sn, "识别到设备端要求结束当前检查任务，准备关闭当前任务状态。", "thought-cancel")
                await self._reply_simple(sn, "好的，已结束当前检查任务。")
            case "confirm_rectify":
                session.task_state = "rectification_confirmed"
                await self._reply_thought(sn, "识别到设备端已确认整改，准备记录整改状态并提示可继续检查。", "thought-confirm")
                await self._reply_simple(sn, "好的，已记录整改确认。如需继续检查，请随时告诉我。")
            case "ask_flow" | "start_check":
                await self._handle_flow_intent(sn, text, intent)
            case "ask_hazard":
                await self._handle_hazard_question(sn, text)
            case _:
                check_subject = self._extract_check_subject(text)
                if check_subject:
                    session.current_task = check_subject
                    session.task_state = "waiting_photo"
                    await self._reply_simple(sn, f"好的，已记录检查目标：{check_subject}。请拍摄现场图片上传，我收到后会按该目标分析。")
                else:
                    await self._reply_simple(sn, "请说明要检查的对象，或拍摄现场图片后再让我分析。")

    async def handle_jpeg(self, sn: str, path: Path) -> None:
        session = broadcaster.get(sn)
        session.latest_image = path.name
        session.task_state = "photo_received"
        await broadcaster.publish(sn, {"type": "device_image", "file": path.name, "url": f"/api/files/{path.name}"})

        validation = image_service.validate_basic(path)
        if not validation.get("valid"):
            await self._reply_image_error(sn, path.name, validation)
            return

        await self._reply_text_only(sn, IMAGE_RECEIVED_REPLY, "agent-image-received")

        check_subject = self._extract_check_subject(session.last_user_input)
        if check_subject:
            thought_text = f"正在检查图片场景和清晰度，并结合{check_subject}检查项判断是否存在隐患。"
            prompt = (
                f"请分析现场图片是否存在{check_subject}相关的施工安全隐患。"
                "请先输出安全隐患分析，最后必须给出整改方案。"
            )
        else:
            thought_text = "正在检查图片场景和清晰度，并按照施工现场通用安全规范判断是否存在隐患。"
            prompt = (
                "请分析现场图片是否存在施工安全隐患。"
                "请检查人员防护、设备状态、现场环境等方面的安全问题。"
                "请先输出安全隐患分析，最后必须给出整改方案。"
            )
        await self._reply_thought(sn, thought_text, "thought-image-analysis")

        session.task_state = "analyzing"
        await broadcaster.publish(sn, {"type": "agent_status", "status": "llm_processing", "message": "正在分析现场图片"}, persist=False)

        # 静态模式（演示用）：不调用流式，直接走固定回复模板
        if not settings.use_dynamic_reply:
            try:
                result = await llm_service.analyze_multimodal(prompt, str(path), self._context(sn))
            except Exception as exc:
                logger.error("[模型分析] Qwen3-VL 分析失败：%s", exc)
                await self._speak_error(sn, ErrorCode.LLM_TIMEOUT, "智能分析暂时失败，请稍后重试，并按检查流程人工复核。")
                return
            session.latest_hazard_result = result
            session.task_state = "hazard_found" if result.get("risk_found") else "waiting_rectification"
            await self._reply_llm_result(sn, result)
            return

        # 动态模式：开启思考流，把网关吐出的每个 token 原样转发到思考气泡（打字机效果）。
        # 每个 delta 用 persist=False，避免上百个 token 撑爆历史；完整文本在结束时统一入库。
        msgid = "llm-stream-analysis"
        await broadcaster.publish(sn, {"type": "agent_thought_start", "message_id": msgid})
        tts_queue: asyncio.Queue[str | None] = asyncio.Queue()
        tts_buffer: list[str] = []
        tts_started = False
        tts_task: asyncio.Task | None = None

        async def tts_parts():
            while True:
                sentence = await tts_queue.get()
                if sentence is None:
                    return
                try:
                    async for item in tts_service.iter_synthesize_pcm_parts(sentence):
                        yield item
                finally:
                    tts_queue.task_done()

        async def queue_tts_text(text: str, force: bool = False) -> None:
            nonlocal tts_started, tts_task
            if text:
                tts_buffer.append(text)

            while True:
                pending = "".join(tts_buffer).strip()
                if not pending:
                    tts_buffer.clear()
                    return

                split_at = self._stream_tts_split_index(pending, force)
                if split_at < 0:
                    tts_buffer[:] = [pending]
                    return

                sentence = pending[:split_at + 1].strip()
                rest = pending[split_at + 1:].strip()
                tts_buffer[:] = [rest] if rest else []
                if sentence:
                    await tts_queue.put(sentence)
                    if not tts_started:
                        tts_started = True
                        tts_task = await broadcaster.send_voice_stream(sn, tts_parts(), "大模型实时语音播报")

                if not rest:
                    return

        async def on_chunk(chunk: str) -> None:
            await broadcaster.publish(
                sn,
                {"type": "agent_thought_delta", "message_id": msgid, "content": chunk},
                persist=False,
            )
            await queue_tts_text(chunk)

        try:
            result = await llm_service.analyze_multimodal(prompt, str(path), self._context(sn), on_chunk=on_chunk)
        except Exception as exc:
            logger.error("[模型分析] Qwen3-VL 分析失败：%s", exc)
            await tts_queue.put(None)
            await broadcaster.publish(sn, {"type": "agent_thought_done", "message_id": msgid})
            await self._speak_error(sn, ErrorCode.LLM_TIMEOUT, "智能分析暂时失败，请稍后重试，并按检查流程人工复核。")
            return
        await queue_tts_text("", force=True)
        await tts_queue.put(None)

        # 结束思考流：带上完整文本，持久化入库供历史回看
        full_text = result.get("web_summary", "")
        await broadcaster.publish(sn, {"type": "agent_thought_done", "message_id": msgid, "content": full_text})

        session.latest_hazard_result = result
        session.task_state = "hazard_found" if result.get("risk_found") else "waiting_rectification"
        if full_text:
            session.last_agent_reply = full_text

        # Web 端先展示后续提示；语音仍按摘要 -> 提示的顺序下发。
        if full_text:
            await self._reply_text_only(sn, FOLLOW_UP_PROMPT, "agent-follow-up")
            if tts_task is not None:
                await tts_task
            await self._speak(sn, FOLLOW_UP_PROMPT)
        else:
            if tts_task is not None:
                await tts_task
            await self._reply_simple(sn, FOLLOW_UP_PROMPT)

    async def _handle_flow_intent(self, sn: str, text: str, intent: dict[str, Any]) -> None:
        session = broadcaster.get(sn)
        flow = knowledge_service.match_check_flow(text)
        if flow:
            session.current_task = flow.get("name")
            session.task_state = "flow_sent"
            logger.info("[知识匹配] 命中本地流程：%s", flow.get("name"))
            await self._reply_thought(sn, f"识别到用户在询问检查内容，正在从本地文档匹配{flow.get('name')}。", "thought-flow")
            await self._reply_local_flow(sn, flow)
            session.task_state = "waiting_photo"
            return

        await self._reply_thought(sn, "本地文档未匹配到明确流程，准备尝试联网检索并返回可参考结果。", "thought-search")
        await broadcaster.publish(sn, {"type": "agent_status", "status": "searching", "message": "本地未命中流程，正在尝试联网检索"}, persist=False)
        results = await search_service.search(text, limit=5)
        if not results:
            await self._reply_simple(sn, settings.search_failed_reply)
            return
        await broadcaster.publish(sn, {"type": "web_flow_result", "source": "web_search", "query": text, "references": results, "tts_reply": settings.local_flow_not_found_reply})
        await self._reply_simple(sn, settings.local_flow_not_found_reply)

    async def _handle_hazard_question(self, sn: str, text: str) -> None:
        session = broadcaster.get(sn)
        if not session.latest_image:
            session.task_state = "waiting_photo"
            await self._reply_simple(sn, "请先拍摄三级配电箱内部线路图片，我收到图像后再协助分析。")
            return
        path = storage_service.find_uploaded(session.latest_image)
        if path is None:
            await self._reply_simple(sn, "最近图片文件不存在，请重新拍摄上传。")
            return
        await self.handle_jpeg(sn, path)

    async def _reply_local_flow(self, sn: str, flow: dict[str, Any]) -> None:
        msgid = f"agent-flow-{flow['id']}"
        items = flow.get("flow", [])
        summary = flow.get("tts_reply") or f"{flow.get('name')}有{len(items)}项内容，请按照内容逐项检查。"
        voice_text = self._local_flow_voice_text(summary, items)
        photo_subject = flow.get("object_name") or flow.get("name") or "检查对象"
        photo_prompt = f"请前往需检查的{photo_subject}现场，并拍摄图像上传"
        await broadcaster.publish(sn, {"type": "agent_start", "message_id": msgid})
        await broadcaster.publish(sn, {
            "type": "local_flow_result",
            "source": "local_docx",
            "flow_id": flow.get("id"),
            "title": flow.get("name"),
            "object_name": flow.get("object_name"),
            "section_no": flow.get("section_no"),
            "score_text": flow.get("score_text"),
            "items": items,
            "tts_reply": voice_text,
        })
        for chunk in self._chunks(summary):
            await broadcaster.publish(sn, {"type": "agent_delta", "message_id": msgid, "content": chunk})
            await asyncio.sleep(STREAM_DELAY_SECONDS)
        await broadcaster.publish(sn, {"type": "agent_done", "message_id": msgid})
        if not await self._speak(sn, voice_text, wait=True):
            return
        await self._reply_simple(sn, photo_prompt)

    async def _reply_llm_result(self, sn: str, result: dict[str, Any]) -> None:
        if settings.use_dynamic_reply:
            # 动态模式：使用 LLM 返回的内容
            web_summary = result.get("web_summary", "")
            tts_reply = result.get("tts_reply", "")
            detail_text = ""  # 初始化变量

            # 如果没有 web_summary，尝试从其他字段构建
            if not web_summary:
                # 尝试从 safety_issues / safety_hazards / risk_items 等字段构建摘要
                issues = result.get("safety_issues") or result.get("safety_hazards") or result.get("risk_items") or []

                if issues and isinstance(issues, list) and len(issues) > 0:
                    # 有隐患
                    issue_count = len(issues)
                    categories = set()
                    descriptions = []

                    for issue in issues:
                        if isinstance(issue, dict):
                            category = issue.get("category", "")
                            desc = issue.get("description", "") or issue.get("name", "")
                            if category:
                                categories.add(category)
                            if desc:
                                descriptions.append(desc)

                    # 构建摘要
                    if categories:
                        category_text = "、".join(list(categories)[:3])
                        web_summary = f"发现 {issue_count} 处安全隐患，涉及{category_text}等方面，请依据相关安全规范进行整改。"
                    else:
                        web_summary = f"发现 {issue_count} 处安全隐患，请依据相关安全规范进行整改。"

                    tts_reply = web_summary[:80]

                    # 构建详细内容
                    detail_lines = []
                    for i, issue in enumerate(issues[:5], 1):  # 最多显示5条
                        if isinstance(issue, dict):
                            category = issue.get("category", "")
                            desc = issue.get("description", "") or issue.get("name", "")
                            risk_level = issue.get("risk_level", "")

                            if category and desc:
                                level_text = f"[{risk_level}]" if risk_level else ""
                                detail_lines.append(f"{i}. {category}{level_text}：{desc}")
                            elif desc:
                                detail_lines.append(f"{i}. {desc}")

                    detail_text = "\n".join(detail_lines) if detail_lines else ""

                    logger.info("[动态回复] 摘要=%s, 详细内容行数=%d", web_summary, len(detail_lines))
                else:
                    # 没有隐患
                    web_summary = "暂未发现明显安全隐患，请继续按照规范进行人工复核。"
                    tts_reply = web_summary
                    logger.info("[动态回复] 无隐患，摘要=%s", web_summary)
            else:
                logger.info("[动态回复] 使用 LLM 原始摘要=%s", web_summary)

            # 发送摘要（流式输出）
            await self._reply_text_only(sn, web_summary, "agent-hazard-summary")

            # 发送详细内容（如果有）
            if detail_text:
                await self._reply_simple(sn, detail_text, speak_text=tts_reply)
            else:
                # 没有详细内容，尝试整改建议
                rectification = result.get("rectification", [])
                if rectification and isinstance(rectification, list):
                    rectification_text = "\n".join(f"{i+1}. {item}" for i, item in enumerate(rectification))
                    await self._reply_simple(sn, rectification_text, speak_text=tts_reply)
                else:
                    await self._speak(sn, tts_reply)

            # 后续提示
            await self._reply_simple(sn, FOLLOW_UP_PROMPT)
        else:
            # 静态模式：使用固定回复
            await self._reply_text_only(sn, HAZARD_SUMMARY_REPLY, "agent-hazard-summary")
            await self._reply_simple(sn, RECTIFICATION_GUIDE, speak_text=HAZARD_SUMMARY_REPLY)
            await self._reply_simple(sn, FOLLOW_UP_PROMPT)

    async def _reply_image_error(self, sn: str, filename: str, validation: dict[str, Any]) -> None:
        code = validation.get("error_code", ErrorCode.IMAGE_POOR_QUALITY)
        message = validation.get("message", "图片无法分析，请重新拍摄。")
        reply = validation.get("tts_reply", message)
        event_type = "invalid_image_scene" if code == ErrorCode.IMAGE_INVALID_SCENE else "poor_image_quality"
        await self._reply_thought(sn, "图片基础校验未通过，先提示设备端重新采集，再停止本次隐患判断。", f"thought-image-error-{Path(filename).stem}")
        await broadcaster.publish(sn, {"type": event_type, "error_code": code, "file": filename, "message": message})
        await self._reply_simple(sn, message, speak_text=reply)

    async def _speak_error(self, sn: str, code: str, text: str) -> None:
        await broadcaster.publish(sn, {"type": "system_error", "error_code": code, "message": text})
        await self._speak(sn, text)

    async def _reply_simple(self, sn: str, text: str, speak_text: str | None = None) -> None:
        msgid = "agent-simple"
        await self._reply_text_only(sn, text, msgid)
        await self._speak(sn, speak_text or text)

    async def _reply_text_only(self, sn: str, text: str, msgid: str) -> None:
        await broadcaster.publish(sn, {"type": "agent_start", "message_id": msgid})
        for chunk in self._chunks(text):
            await broadcaster.publish(sn, {"type": "agent_delta", "message_id": msgid, "content": chunk})
            await asyncio.sleep(STREAM_DELAY_SECONDS)
        await broadcaster.publish(sn, {"type": "agent_done", "message_id": msgid})

    async def _reply_thought(self, sn: str, text: str, msgid: str) -> None:
        await broadcaster.publish(sn, {"type": "agent_thought_start", "message_id": msgid})
        for chunk in self._chunks(text):
            await broadcaster.publish(sn, {"type": "agent_thought_delta", "message_id": msgid, "content": chunk})
            await asyncio.sleep(STREAM_DELAY_SECONDS)
        await broadcaster.publish(sn, {"type": "agent_thought_done", "message_id": msgid})

    def _chunks(self, text: str) -> list[str]:
        if not text:
            return [""]
        return [text[index:index + STREAM_CHARS] for index in range(0, len(text), STREAM_CHARS)]

    def _voice_summary(self, text: str, max_len: int = 120) -> str:
        """把分条目的格式化文本压成一句适合语音播报的摘要。"""
        cleaned = text.replace("【", "").replace("】", "：")
        # 多个空行 / 换行合并成句号、逗号
        cleaned = re.sub(r"\n{2,}", "。", cleaned)
        cleaned = cleaned.replace("\n", "，")
        cleaned = re.sub(r"[，。]{2,}", "。", cleaned).strip("，。 ")
        if len(cleaned) > max_len:
            cleaned = cleaned[:max_len - 1] + "…"
        return cleaned or "分析完成，请结合现场情况人工复核。"

    def _local_flow_voice_text(self, summary: str, items: list[Any]) -> str:
        lines = [summary.strip()] if summary.strip() else []
        for index, item in enumerate(items, start=1):
            text = str(item).strip()
            if not text:
                continue
            text = re.sub(r"^\d+[\.\、]\s*", "", text)
            lines.append(f"第{index}项，{text}")
        return "\n".join(lines)

    def _stream_tts_split_index(self, text: str, force: bool = False) -> int:
        if not text:
            return -1
        search_to = min(len(text), LLM_TTS_MAX_CHARS)
        for index, char in enumerate(text[:search_to]):
            if char in LLM_TTS_ENDINGS and index + 1 >= LLM_TTS_MIN_CHARS:
                return index
        soft_indexes = [index for index, char in enumerate(text[:search_to]) if char in LLM_TTS_SOFT_BREAKS]
        if soft_indexes and (force or len(text) >= LLM_TTS_MAX_CHARS):
            return soft_indexes[-1]
        if len(text) >= LLM_TTS_MAX_CHARS:
            return LLM_TTS_MAX_CHARS - 1
        if force:
            return len(text) - 1
        return -1

    async def _speak(self, sn: str, text: str, wait: bool = False) -> bool:
        session = broadcaster.get(sn)
        started_generation = session.tts_generation
        session.last_agent_reply = text

        async def parts():
            try:
                async for item in tts_service.iter_synthesize_pcm_parts(text):
                    if broadcaster.get(sn).tts_generation != started_generation + 1:
                        logger.info("[语音合成] 已被新一轮输入取消，停止后续分句 TTS")
                        return
                    yield item
            except Exception as exc:
                if broadcaster.get(sn).tts_generation != started_generation + 1:
                    logger.info("[语音合成] 已被新一轮输入取消，忽略过期 TTS 失败：%s", exc)
                    return
                logger.error("[语音合成] TTS 失败：%s", exc)
                await broadcaster.publish(sn, {"type": "tts_failed", "error_code": ErrorCode.TTS_FAILED, "message": "TTS 合成失败，结果已在 Web 端展示，但未下发设备语音。"})

        if broadcaster.get(sn).tts_generation != started_generation:
            logger.info("[语音合成] 已被新一轮输入取消，忽略过期 TTS 结果")
            return False
        task = await broadcaster.send_voice_stream(sn, parts(), text)
        if wait:
            await task
        return True

    def _context(self, sn: str) -> dict[str, Any]:
        session = broadcaster.get(sn)
        return {
            "current_task": session.current_task,
            "task_state": session.task_state,
            "latest_image": session.latest_image,
            "latest_hazard_result": session.latest_hazard_result,
            "last_agent_reply": session.last_agent_reply,
        }

    def _extract_check_subject(self, user_input: str) -> str | None:
        """从用户输入中提取检查对象，若未提取到则返回 None"""
        if not user_input:
            return None

        # 简单的关键词提取：寻找"检查"、"查"、"看"等动词后的内容
        keywords = ["检查", "查", "看看", "分析", "识别"]
        for keyword in keywords:
            if keyword in user_input:
                idx = user_input.index(keyword)
                subject = user_input[idx + len(keyword):].strip()
                # 清理标点和常见尾缀
                subject = subject.rstrip("。，！？、")
                if subject and len(subject) <= 20:
                    return subject

        # 如果包含常见检查对象关键词，直接提取
        check_objects = ["三级配电箱", "配电箱", "线路", "脚手架", "安全带", "安全帽"]
        for obj in check_objects:
            if obj in user_input:
                return obj

        return None


agent_service = AgentService()
