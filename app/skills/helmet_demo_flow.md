name: helmet-demo-flow
version: 1.0.0
description: 链博会智能头盔演示流程。

steps:
1. 班组长/安全员戴上智能头盔。
2. 用户询问三级配电箱线路检查流程。
3. Agent 返回七项检查流程并播报。
4. Agent 提示前往现场并拍照。
5. 用户上传三级配电箱现场图片。
6. Agent 分析隐患。
7. Agent 推送整改建议并播报。
8. 用户确认立即整改。
9. 展示结束。

error_branches:
- ASR 识别失败。
- 非施工图片。
- 模糊图片。
- TTS 播报中断。
- voice 通道未连接。
