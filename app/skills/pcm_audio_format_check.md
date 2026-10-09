name: pcm-audio-format-check
version: 1.0.0
description: ASR/TTS 音频格式要求和排查要点。

format:
- pcm_s16le
- 16000 Hz
- mono
- 16-bit
- little-endian

asr:
- 裸 pcm 可临时封装为 wav 后送 FunASR。
- 空文本、噪声或低置信度应触发 asr_failed。

tts:
- ModelScope TTS 输出若为 wav，需要剥离 wav header。
- voice websocket 按固定 chunk 分片发送。
- 新 pcm 到达时必须中断当前 TTS。
