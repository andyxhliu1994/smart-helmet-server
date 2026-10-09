import os

import requests


base_url = os.environ.get("LLM_BASE_URL", "").rstrip("/")
image_url = os.environ.get("TEST_IMAGE_URL", "").strip()
if not base_url or not image_url:
    raise SystemExit("请先设置 LLM_BASE_URL 和 TEST_IMAGE_URL")

headers = {"Content-Type": "application/json"}
api_key = os.environ.get("LLM_API_KEY", "").strip()
if api_key:
    headers["Authorization"] = f"Bearer {api_key}"

payload = {
    "model": os.environ.get("LLM_MODEL", "Qwen3-VL-32B-Instruct"),
    "messages": [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "请判断测试图片中是否存在未正确佩戴安全头盔的人员。"},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        }
    ],
    "temperature": 0,
    "stream": False,
}

response = requests.post(
    f"{base_url}/chat/completions",
    json=payload,
    headers=headers,
    timeout=120,
)
response.raise_for_status()
print(response.text)
