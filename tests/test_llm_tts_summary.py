from __future__ import annotations

import unittest

from app.services.llm import _tts_summary


class TtsSummaryTest(unittest.TestCase):
    def test_extracts_explicit_voice_summary(self) -> None:
        text = "语音摘要：未发现配电箱隐患，请补拍配电箱内部。\n\n一、安全隐患分析\n完整报告"

        self.assertEqual(_tts_summary(text), "未发现配电箱隐患，请补拍配电箱内部")

    def test_fallback_is_short_and_removes_markdown(self) -> None:
        text = "**一、安全隐患分析**\n" + "发现线路杂乱。" * 30

        summary = _tts_summary(text)

        self.assertLessEqual(len(summary), 80)
        self.assertNotIn("*", summary)


if __name__ == "__main__":
    unittest.main()
