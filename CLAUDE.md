# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

本文件为 Claude Code 在 `agent/` 目录中工作时提供指引。

> 上层的 `../CLAUDE.md` 介绍了整个 `data/` 文件夹，并给出本 `agent/` 项目的「大图景」总览（`Broadcaster` 中枢、三类 WebSocket 通道、可切换的 provider 服务、基于 generation 的 TTS 打断）。**请先读它。** 本文件只补充那份总览里看不出来、必须读代码才能知道的细节。

## 命令

所有命令均在 `agent/` 目录下执行：

```powershell
pip install -r requirements.txt   # funasr/modelscope/torch 体量很大，仅非 mock provider 才需要
copy .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 18787
python tools/run_main_service.py  # 另一种启动：chdir 到根目录，日志写到 uvicorn.out.log / uvicorn.err.log
```

**没有配置任何测试套件、linter 或 formatter** —— `tests/` 里只有夹具数据。「测试」就是对着运行中的服务跑 demo 客户端：

```powershell
python tools/demo_client.py --server http://127.0.0.1:18787 --sn rv1126b --auto
python tools/demo_client.py --server http://127.0.0.1:18787 --sn rv1126b --auto --play-voice
python tools/pcm_broadcast_server.py --host 0.0.0.0 --port 18888   # 独立 PCM 广播测试服务器
```

demo 客户端支持截图 / MP4 / 浏览器录制参数（`--screenshot-dir`、`--record-mp4`、`--record-browser`、`--hide-web-nav`），完整组合见 `README.md`。合成 MP4 需要 PATH 中有 `ffmpeg`，没有则保留帧目录。

## 请求生命周期（总览没讲的部分）

设备**不会**「POST 一个文件然后等返回」。它是一个**两步、fire-and-forget（发了不等结果）**的流程：

1. `POST /api/upload` 保存文件并 publish 一条 `file_uploaded` 事件 —— 但**不做任何分析**。
2. 设备**随后**通过 `/{sn}/cmd` 发一条 `cmd` JSON 点名该文件。此时 `AgentService.handle_cmd` 才用 `storage_service.find_uploaded` 定位它，并按后缀分发。

`ws_device.device_cmd` 把每个 `handle_cmd` 作为 fire-and-forget 的 `asyncio.create_task` 抛出去（出错只记日志），这样 cmd socket 在分析进行时仍能继续收消息。`/{sn}/voice` socket 是**纯下行**：服务端读入站帧后直接丢弃，PCM 只通过 `send_voice` 往设备方向流。

分发由**实际文件后缀决定，不是 `cmd` 字段**。`handle_cmd` 把 `jpg` 归一成 `jpeg`；如果声明的 `cmd`（`upload_pcm` / `upload_jpeg` / `upload_h264`）和真实后缀对不上，它会告警然后**仍按后缀处理**。`h264` 在本 MVP 中只保存、不分析。

去重 key 是 `f"{sn}:{msgid}:{cmd}:{file}"`，存在 `session.processed_events` 里 —— **永不清除**，是一个伴随会话生命周期、只增不减的 set。

## 意图 → 分支映射

`handle_pcm`：ASR → `asr_service.is_valid_text` 有效性闸门 → `intent_service.classify`（**纯关键词子串规则，无模型**）→ 对 intent 做 `match`：

- `repeat` → 重播 `session.last_agent_reply`。
- `cancel` → `task_state = "closed"`。
- `confirm_rectify` → `task_state = "rectification_confirmed"`。
- `ask_flow` / `start_check` → `_handle_flow_intent`：先试 `knowledge_service.match_check_flow`（`app/knowledge/check_flows.json` 里、由 docx 整理来的本地流程），命中不了退回 `search_service.search`（**当前是 stub，恒返回 `[]`**，所以这条路径必然落到 `settings.search_failed_reply`）。
- `ask_hazard` → 若 `session.latest_image` 存在则重新分析它，否则提示先拍照。

`match_check_flow` 自身还有个前置闸门：文本里**必须含「流程 / 怎么查 / 怎么检查 / 检查」之一**才会进入打分匹配，且最终得分需 ≥ 2 才算命中。

`task_state` 是在 `AgentService` 各处随手改的**自由字符串**（`photo_received`→`analyzing`→`hazard_found` / `waiting_rectification` 等），**不是枚举**，也没有任何东西校验状态迁移。依赖某个状态前，先全局搜一下这个字符串字面量。

## 回复发送的双轨模式

每条 agent 回复都为同一内容走**两条并行轨道**：

- **网页轨**：`agent_start` → 多个 `agent_delta`（按 `STREAM_CHARS=6` 字切片，间隔约 `0.04s`）→ `agent_done`。「思考」用平行的 `agent_thought_start/_delta/_done` 事件族。
- **设备语音轨**：`_speak` → `tts_service.synthesize_pcm` → `broadcaster.send_voice`。

`_speak(..., wait=True)` 会 await 语音任务，确保下一条回复不会提前递增 TTS generation、把这段音频拦腰打断 —— `_reply_local_flow` 在发出后续「拍照提示」前就用了它。新增回复时请走这些 helper（`_reply_simple`、`_reply_text_only`、`_reply_thought`、`_speak`），**不要手动 publish 裸的 `agent_*` 事件**，否则两条轨会失同步。

图片分支（`handle_jpeg`）在调模型前会先过 `image_service.validate_basic` 做基础校验：文件过小 → `IMAGE_POOR_QUALITY`；文件名 / 内容含 `non_site` / `invalid_scene` / `NON_SITE_MARKER` → `IMAGE_INVALID_SCENE`；含 `blurry` / `blur` / `poor` / `BLURRY_MARKER` → `IMAGE_POOR_QUALITY`。校验不过直接走 `_reply_image_error`，不调用 LLM。

## 结果 schema 被强制守卫

`llm_service` 的两条路径（mock 与 `openai_compatible`）都经 `result_guard.normalize_hazard_result`（`app/services/schemas.py`）返回，它填默认值、**永远把 `need_manual_review` 强制为 `true`**、并把 `tts_reply` 截断到 80 字（超出补 `...`）。任何新的视觉结论都必须保留这个守卫 —— 产品要求是「AI 隐患判定永远不作为最终结论」。修改这块属于安全相关逻辑，改动前请提示影响。

LLM 输出非法 / 非 JSON 时不会抛异常：`_openai_chat` 把它包成 `risk_found=false` 的结果，原始文本塞进 `web_summary`。

## 网关 provider 对响应结构容错

`http_gateway` 的 ASR / TTS 对接的是 JSON 结构未知的外部端点，所以这两个服务都用递归的 `_find_first(payload, keys)` 在任意深度里捞第一个匹配 key（`url` / `text` / `audio` / `data` / …），并回退到 base64 解码或对返回的 URL 再发一次 GET。这些网关的鉴权是**裸的 `token: <value>` 请求头，不是 `Authorization`**。所有音频路径最终都过 `_strip_wav_header`，保证设备只收到裸 PCM。

## 值得在代码里反复确认的约定

- 文件名必须匹配 `storage_service.FILE_RE`：`^{sn}-{数字}.(pcm|jpeg|jpg|h264)$`。**同名重传会被拒绝**（不覆盖）。文件存于 `app/uploads/<type>/`，合成的 TTS 存于 `app/uploads/tts/`。
- mock provider 是确定性的测试钩子，用来编排 demo：mock ASR 返回固定文本，但会根据 PCM 字节 / 文件名里的 ASCII 标记切换（`REPEAT` / `CONFIRM`，以及含 `empty` 时返回空）；mock LLM 在图片名含 `no_hazard` 时返回「无隐患」。
- 历史是 append-only 的 JSONL：`app/data/conversations/{sn}.jsonl`，每行一条 publish 事件，按 `session_id` 分组成会话。时间戳用东八区（UTC+8，`now_iso`）。`publish(..., persist=False)` 会跳过历史，用于瞬时 / 状态类事件。
- 网页环形缓冲（`broadcaster.buffers`）每个 sn 保留最近 100 条事件，并重放给每个新连接的网页端。
- `require_admin` **仅当 `ADMIN_TOKEN` 非空时**才校验 `Authorization: Bearer <ADMIN_TOKEN>`；空 token（`.env.example` 默认值）完全关闭 admin 鉴权。该函数在 `admin.py` / `knowledge.py` / `history.py` 里**各有一份拷贝**，改动需同步三处。

## 安全提示

`/{sn}/cmd`、`/{sn}/voice`、`/ws/web/{sn}`、`/api/upload` **没有任何鉴权** —— 任何知道 `sn` 的客户端都能驱动一台设备或读取它的事件流。这对 MVP / 演示可接受，但联网部署前必须修复。
