name: qwen-vl-hazard-json-guard
version: 1.0.0
description: Qwen3-VL 隐患分析输出 JSON 校验规则。

required_fields:
- intent
- risk_found
- risk_items
- rectification
- need_manual_review
- web_summary
- tts_reply

rules:
- 不允许输出 Markdown。
- tts_reply 控制在 80 个中文字符以内。
- 视觉模型判断必须包含 need_manual_review=true。
- 非施工图片不能输出隐患结论。
- 图片质量差必须输出 need_retake=true。
