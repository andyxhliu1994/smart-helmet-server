import os

import requests


url = os.environ.get("TTS_GATEWAY_URL", "").strip()
if not url:
    raise SystemExit("请先设置 TTS_GATEWAY_URL")

headers = {"Content-Type": "application/json", "Cache-Control": "no-cache"}
token = os.environ.get("TTS_GATEWAY_TOKEN", "").strip()
if token:
    headers["token"] = token

response = requests.post(
    url,
    json={"input": "你好，这是一次语音合成测试。", "response_format": "pcm"},
    headers=headers,
    timeout=60,
)
response.raise_for_status()

with open("output.pcm", "wb") as output:
    output.write(response.content)

print("PCM 文件已保存为 output.pcm")
