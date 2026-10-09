from __future__ import annotations

from typing import Any


class ResultGuard:
    def normalize_hazard_result(self, result: dict[str, Any], add_defaults: bool = True) -> dict[str, Any]:
        """
        规范化隐患检测结果

        Args:
            result: LLM 返回的原始结果
            add_defaults: 是否添加默认的 web_summary 和 tts_reply（静态模式用）
        """
        data = dict(result)
        data.setdefault("intent", "hazard_check")
        data.setdefault("risk_found", False)
        data.setdefault("risk_items", [])
        data.setdefault("rectification", [])
        data.setdefault("need_manual_review", True)

        # 只在需要时添加默认摘要（静态模式）
        if add_defaults:
            data.setdefault("web_summary", "分析完成，请结合现场情况人工复核。")
            data.setdefault("tts_reply", data["web_summary"])
            if len(data["tts_reply"]) > 80:
                data["tts_reply"] = data["tts_reply"][:77] + "..."

        return data


result_guard = ResultGuard()
