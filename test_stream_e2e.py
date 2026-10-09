"""端到端验证思考流式：订阅 web WS → 上传图片 → 发 cmd → 记录 thought_delta 到达时间。

如果流式正常工作，agent_thought_delta 会分散在数十秒内陆续到达；
如果是伪流式（等完整结果再一次性吐），所有 delta 会集中在同一瞬间。
"""
import asyncio
import json
import time
import shutil
from pathlib import Path

import httpx
import websockets

SERVER = "http://127.0.0.1:18787"
WS_BASE = "ws://127.0.0.1:18787"
SN = "rv1126b"
SRC_IMAGE = Path("app/uploads/jpeg/rv1126b-1781505282.jpeg")


async def main():
    # 1. 准备一个新文件名的图片副本（避免同名重传被拒）
    ts = int(time.time() * 1000)
    new_name = f"{SN}-{ts}.jpeg"
    dst = SRC_IMAGE.parent / new_name
    shutil.copy(SRC_IMAGE, dst)
    print(f"[准备] 复制图片 -> {new_name}")

    # 2. 订阅 web WS
    web_url = f"{WS_BASE}/ws/web/{SN}"
    async with websockets.connect(web_url) as web_ws:
        print(f"[订阅] {web_url}")

        # 3. 上传图片（用已存在的副本，直接发 cmd 通知即可，但仍走标准 upload 接口更真实）
        #    这里图片已经在 uploads 目录，find_uploaded 能直接找到，所以只需发 cmd。

        # 4. 通过 cmd WS 发上传通知
        cmd_url = f"{WS_BASE}/{SN}/cmd"
        async with websockets.connect(cmd_url) as cmd_ws:
            print(f"[连接] {cmd_url}")
            payload = {"msgid": ts % 2147483647, "cmd": "upload_jpeg", "file": new_name, "msg": "success"}
            await cmd_ws.send(json.dumps(payload))
            print(f"[发送] cmd 通知：{payload}")

            # 5. 收集事件，统计 thought_delta 到达时间
            t0 = time.monotonic()
            delta_times = []
            thought_text = ""
            deadline = t0 + 120  # 最多等 2 分钟

            while time.monotonic() < deadline:
                try:
                    raw = await asyncio.wait_for(web_ws.recv(), timeout=120)
                except asyncio.TimeoutError:
                    break
                event = json.loads(raw)
                etype = event.get("type")
                now = time.monotonic() - t0

                if etype == "agent_thought_start" and event.get("message_id") == "llm-stream-analysis":
                    print(f"[{now:6.2f}s] >>> 思考流开始")
                elif etype == "agent_thought_delta" and event.get("message_id") == "llm-stream-analysis":
                    delta_times.append(now)
                    content = event.get("content", "")
                    thought_text += content
                    # 只打印前 20 个 delta 的时间，避免刷屏
                    if len(delta_times) <= 20:
                        print(f"[{now:6.2f}s]   delta #{len(delta_times)}: {content!r}")
                elif etype == "agent_thought_done" and event.get("message_id") == "llm-stream-analysis":
                    print(f"[{now:6.2f}s] <<< 思考流结束，共 {len(delta_times)} 个 delta")
                    break
                elif etype == "system_error":
                    print(f"[{now:6.2f}s] [错误] {event.get('message')}")
                    break

            # 6. 分析结果
            print("\n===== 流式分析 =====")
            print(f"总 delta 数：{len(delta_times)}")
            if len(delta_times) >= 2:
                span = delta_times[-1] - delta_times[0]
                print(f"首个 delta：{delta_times[0]:.2f}s，末个 delta：{delta_times[-1]:.2f}s")
                print(f"delta 时间跨度：{span:.2f}s")
                if span > 2.0:
                    print("✅ 真流式：delta 分散在较长时间内陆续到达")
                else:
                    print("❌ 伪流式：所有 delta 集中在很短时间内到达")
            print(f"\n===== 思考全文 =====\n{thought_text}")

    # 清理副本
    dst.unlink(missing_ok=True)
    print(f"\n[清理] 已删除测试图片副本 {new_name}")


if __name__ == "__main__":
    asyncio.run(main())
