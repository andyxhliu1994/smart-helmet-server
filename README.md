# 智能头盔隐患识别 Agent

基于 FastAPI 的智能头盔隐患识别 MVP，支持设备文件上传、WebSocket 通信、手机端对话展示、本地流程知识管理、历史设备对话回看，以及按空格逐步推进的演示客户端。

## 启动

推荐使用 Python 3.12，并为项目建立独立虚拟环境。macOS / Linux：

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-base.txt
cp .env.example .env
python -m uvicorn app.main:app --host 127.0.0.1 --port 18787
```

Windows PowerShell：

```powershell
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements-base.txt
copy .env.example .env
python -m uvicorn app.main:app --host 127.0.0.1 --port 18787
```

`requirements-base.txt` 适用于 Mock 模式以及通过 HTTP 网关访问 ASR、TTS、VLM 的模式。只有需要在本机直接加载 FunASR 或 ModelScope 模型时，才安装包含 PyTorch 的完整依赖：

```bash
python -m pip install -r requirements.txt
```

部署到服务器时可把监听地址改为 `0.0.0.0`，本地开发默认只监听 `127.0.0.1`。

默认使用 mock ASR/LLM/TTS，便于先打通链路。接真实模型时修改 `.env`：

```text
ASR_PROVIDER=funasr
LLM_PROVIDER=openai_compatible
TTS_PROVIDER=modelscope
```

使用测试说明里的外部网关时：

```text
ASR_PROVIDER=http_gateway
EXTERNAL_UPLOAD_URL=
ASR_GATEWAY_URL=
ASR_GATEWAY_TOKEN=按测试说明填写

TTS_PROVIDER=http_gateway
TTS_GATEWAY_URL=
TTS_GATEWAY_RESPONSE_FORMAT=pcm
```

## 页面

- 实时手机对话页：http://127.0.0.1:18787/
- 指定客户端实时对话页：http://127.0.0.1:18787/client/rv1126b
- 本地文档管理页：http://127.0.0.1:18787/knowledge
- 历史设备对话页：http://127.0.0.1:18787/history
- 客户端管理页：http://127.0.0.1:18787/admin
- Web 测试客户端：http://127.0.0.1:18787/test-client

实时对话页按客户端 `sn` 一一对应：

- `/client/{sn}` 会打开指定客户端的实时对话页，例如 `/client/rv1126b`。
- `/?sn={sn}` 也可以打开指定客户端；未传 `sn` 时使用 `.env` 中的 `DEFAULT_SN`。
- 管理页设备列表中的“实时对话”链接会打开对应客户端页面。

实时对话页的“历史 / 文档”按钮默认显示，可通过命令行环境变量隐藏：

```powershell
$env:WEB_NAV_VISIBLE="false"
uvicorn app.main:app --host 0.0.0.0 --port 18787
```

## 测试客户端

浏览器测试客户端可以模拟设备端 `cmd` / `voice` 两个 WebSocket 连接，支持上传 PCM 和图片文件，并内置与实时对话页一致的消息展示区：

```text
http://127.0.0.1:18787/test-client
```

使用流程：

1. 填写客户端 `sn`，点击“连接 cmd / voice”。注意：测试客户端会模拟设备端连接，同一 `sn` 已有真机在线时会占用该 `sn` 的 `cmd` / `voice` 通道。
2. 选择 `.pcm` 文件后点击“上传 PCM 并通知服务端”，页面会先调用 `POST /api/upload`，再向 `/{sn}/cmd` 发送 `upload_pcm`。
3. 选择图片后点击“上传图片并通知服务端”，非 JPEG 图片会在浏览器内转换为 JPEG，再发送 `upload_jpeg`。
4. 如果服务端下发 TTS，页面会从 `/{sn}/voice` 接收 PCM 二进制帧，并提供最近一次语音下载。

命令行演示客户端：

```powershell
python tools/demo_client.py --server http://127.0.0.1:18787 --sn rv1126b
python tools/demo_client.py --server http://127.0.0.1:18787 --sn rv1126b --auto
python tools/demo_client.py --server http://127.0.0.1:18787 --sn rv1126b --auto --play-voice
```

运行后按空格逐步推进 docx 演示流程。收到 TTS 时会保存：

```text
tools/demo_assets/received_voice/voice-001.pcm
tools/demo_assets/received_voice/voice-001.wav
```

生成每一步截图并隐藏页面导航：

```powershell
python tools/demo_client.py --server http://127.0.0.1:18787 --sn rv1126b --auto --screenshot-dir app/data/events/screenshots/demo --hide-web-nav
```

如本机已安装 ffmpeg，可在截图流程结束后合成 MP4：

```powershell
python tools/demo_client.py --server http://127.0.0.1:18787 --sn rv1126b --auto --screenshot-dir app/data/events/screenshots/demo --record-mp4 --mp4-path app/data/events/screenshots/demo/demo_flow.mp4
```

需要录到 agent 流式返回和思考过程时，使用 Chrome 连续录制：

```powershell
python tools/demo_client.py --server http://127.0.0.1:18787 --sn rv1126b --auto --record-browser --browser-record-dir app/data/events/recordings/demo --browser-mp4-path app/data/events/recordings/demo/demo_flow.mp4 --hide-web-nav
```

连续录制会先保存浏览器帧；本机有 ffmpeg 时自动合成 MP4，没有 ffmpeg 时保留帧目录并提示。

## 主服务连接地址

设备端需要建立两个 WebSocket 连接，服务端通过 URL 路径中的 `{sn}` 识别客户端：

```text
ws://127.0.0.1:18787/{sn}/cmd
ws://127.0.0.1:18787/{sn}/voice
```

例如同一台设备 `rv1126b` 应连接：

```text
ws://127.0.0.1:18787/rv1126b/cmd
ws://127.0.0.1:18787/rv1126b/voice
```

`cmd` 和 `voice` 的 `{sn}` 必须一致，服务端才会把它们合并为同一个客户端会话。实时对话网页连接的是：

```text
ws://127.0.0.1:18787/ws/web/{sn}
```

因此三条连接的对应关系是：

```text
设备命令通道：/{sn}/cmd
设备语音通道：/{sn}/voice
网页实时查看：/client/{sn} -> /ws/web/{sn}
```

文件上传仍走 `POST /api/upload`。上传文件名必须带同一个 `sn` 前缀，例如 `rv1126b-1781505282.jpeg`、`rv1126b-1781504464.pcm`、`rv1126b-1781504464.mp3`，服务端会从文件名解析归属客户端。MP3 上传后会先转换成设备协议使用的 `pcm_s16le / 16k / mono` 再进入语音识别链路。

## PCM 广播测试服务器

启动一个独立 WebSocket 测试服务器，向所有已连接客户端广播 PCM 二进制帧：

```powershell
python tools/pcm_broadcast_server.py --host 0.0.0.0 --port 18888
```

默认生成 16k/16bit/mono 的 440Hz 测试音并循环广播。广播本地 PCM 文件：

```powershell
python tools/pcm_broadcast_server.py --port 18888 --pcm tools/demo_assets/received_voice/voice-001.pcm
```

客户端连接：

```text
ws://127.0.0.1:18888
```

## 主服务客户端管理

主服务启动后打开客户端管理页：

```text
http://127.0.0.1:18787/admin
```

页面可查看当前设备的 `cmd` / `voice` 连接状态，并支持：

- 上传 PCM/WAV/MP3 文件，主动下发到指定设备的 `voice` 通道；MP3 会自动转换为设备协议使用的 PCM。
- 输入大模型流式返回文字，在实时对话页按分片展示，并合成为 TTS 语音下发到指定设备。
- 主动中断指定设备当前语音下发。
- 兼容 `websocket-cmd.py` 的控制台操作：向 `/{sn}/cmd` 下发 `start_pcm`、`stop_pcm`、`start_h264`、`stop_h264`、`snap_jpeg`、`reboot`、`uptime`，并可选择只向 `/{sn}/voice` 发送裸 PCM 二进制。

如果服务端配置了 `ADMIN_TOKEN`，需要先在管理页填写并保存相同 Token，管理接口会校验 `Authorization: Bearer <token>`。
