# 测试夹具规划

当前 demo_client 会自动生成最小测试素材到 `tools/demo_assets/`。

建议后续替换为真实素材：

- `audio/ask_flow.pcm`：询问三级配电箱线路检查流程。
- `audio/empty.pcm`：空音频，用于 ASR 失败。
- `audio/noise.pcm`：噪声音频。
- `images/valid_panel.jpeg`：三级配电箱现场图片。
- `images/non_site.jpeg`：非施工场所图片。
- `images/blurry_panel.jpeg`：模糊配电箱图片。
- `images/no_hazard_panel.jpeg`：未发现明显隐患图片。
- `images/hazard_spacing.jpeg`：线路间距疑似不合规图片。

验收重点：

- ASR 成功/失败分支。
- 本地流程匹配。
- 图片异常分支。
- 隐患 JSON 输出。
- TTS 中断和 voice 下发。
