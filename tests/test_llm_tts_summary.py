from __future__ import annotations

import unittest

from app.services.llm import _tts_summary, _with_report_notice


class TtsSummaryTest(unittest.TestCase):
    def test_extracts_explicit_voice_summary(self) -> None:
        text = "语音摘要：未发现配电箱隐患，请补拍配电箱内部。\n\n一、安全隐患分析\n完整报告"

        self.assertEqual(_tts_summary(text), "未发现配电箱隐患，请补拍配电箱内部")

    def test_fallback_is_short_and_removes_markdown(self) -> None:
        text = "**一、安全隐患分析**\n" + "发现线路杂乱。" * 30

        summary = _tts_summary(text)

        self.assertLessEqual(len(summary), 80)
        self.assertNotIn("*", summary)

    def test_adds_report_notice_when_hazard_found(self) -> None:
        summary = _with_report_notice("发现线路杂乱，请立即整理", risk_found=True)

        self.assertEqual(summary, "发现线路杂乱，请立即整理。完整隐患分析和整改方案已推送，请查看。")

    def test_does_not_claim_report_when_no_hazard_found(self) -> None:
        summary = _with_report_notice("未发现明显隐患，请补拍关键部位", risk_found=False)

        self.assertNotIn("整改方案已推送", summary)


if __name__ == "__main__":
    unittest.main()
