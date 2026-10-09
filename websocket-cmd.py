import asyncio
import websockets
import threading
import queue
import json
from datetime import datetime

# 保存客户端
# {
#     "/device1": {ws1, ws2},
#     "/device2": {ws3}
# }
clients = {}
# 键盘输入队列
cmd_queue = queue.Queue()

async def register(websocket, path):
    """客户端注册"""
    if path not in clients:
        clients[path] = set()
        
    clients[path].add(websocket)
    print(f"\n[连接] {websocket.remote_address} -> {path}")
    print_online()

    try:
        async for message in websocket:
            print(f"\n[收到]{path}: {message}")
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        if path in clients:
            clients[path].discard(websocket)
            if len(clients[path]) == 0:
                del clients[path]
        print(f"\n[断开] {websocket.remote_address}")
        print_online()

def print_online():
    print("\n===== 在线客户端 =====")
    total = 0
    for path, ws_set in clients.items():
        print(f"{path}: {len(ws_set)}")
        total += len(ws_set)
    print(f"总连接数: {total}")
    print("=====================\n")

async def send_cmd_to_path(path, msg):
    """发送给指定path"""
    if path not in clients:
        print(f"[错误] 没有客户端连接到 {path}")
        return
    dead = []
    for ws in clients[path]:
        try:
            await ws.send(msg)
        except:
            dead.append(ws)
    for ws in dead:
        clients[path].discard(ws)
    print(f"[发送] -> {path} : {msg}")


async def send_file_to_path(path, filename):
    """
    发送二进制文件
    """
    if path not in clients:
        print(f"[错误] 没有客户端连接到 {path}")
        return
    try:
        with open(filename, "rb") as f:
            data = f.read()
        print(f"[文件] {filename}")
        print(f"[大小] {len(data)} Bytes")
    except Exception as e:
        print("打开文件失败:", e)
        return
    dead = []

    for ws in clients[path]:
        try:
            await ws.send(data)
        except Exception as e:
            print("发送失败:", e)
            dead.append(ws)

    for ws in dead:
        clients[path].discard(ws)

    print(f"[完成] 文件已发送到 {path}")

async def broadcast(msg):
    """广播"""
    dead = []
    for path in clients:
        for ws in clients[path]:
            try:
                await ws.send(msg)
            except:
                dead.append((path, ws))

    for path, ws in dead:
        clients[path].discard(ws)
    print(f"[广播] {msg}")


def keyboard_thread():
    """
    键盘菜单线程
    """
    menu = """
==========================
1 -> 发送 录音 启动 命令
2 -> 发送 录音 停止 命令
3 -> 发送 录像 启动 命令
4 -> 发送 录像 停止 命令
5 -> 发送 截图      命令
6 -> 发送 重启      命令
7 -> 发送音频文件到/voice
8 -> 广播时间
9 -> 自定义发送
q -> 退出
==========================
"""
    while True:
        print(menu)
        cmd = input("请选择: ").strip()
        cmd_queue.put(cmd)
        if cmd == "q":
            break

async def process_command():
    """
    处理键盘命令
    """

    while True:
        while not cmd_queue.empty():
            cmd = cmd_queue.get()

            if cmd == "1":
                await send_cmd_to_path(
                    "/rv1126b/cmd",
                    json.dumps({
                        "cmd": "start_pcm",
                        "msgid": 11
                    })
                )
            elif cmd == "2":
                await send_cmd_to_path(
                    "/rv1126b/cmd",
                    json.dumps({
                        "cmd": "stop_pcm",
                        "msgid": 12
                    })
                )
            elif cmd == "3":
                await send_cmd_to_path(
                    "/rv1126b/cmd",
                    json.dumps({
                        "cmd": "start_h264",
                        "msgid": 21
                    })
                )
            elif cmd == "4":
                await send_cmd_to_path(
                    "/rv1126b/cmd",
                    json.dumps({
                        "cmd": "stop_h264",
                        "msgid": 22
                    })
                )
            elif cmd == "5":
                await send_cmd_to_path(
                    "/rv1126b/cmd",
                    json.dumps({
                        "cmd": "snap_jpeg",
                        "msgid": 33
                    })
                )
            elif cmd == "6":
                await send_cmd_to_path(
                    "/rv1126b/cmd",
                    json.dumps({
                        "cmd": "reboot",
                        "msgid": 44
                    })
                )
            elif cmd == "8":
                await broadcast(
                    json.dumps({
                        "cmd": "uptime",
                        "time": datetime.now().strftime(
                            "%Y-%m-%d %H:%M:%S"),
                        "msgid": 55
                    })
                )
            elif cmd == "9":
                path = input("path:")
                msg = input("message:")
                await send_cmd_to_path(path, msg)
        
            elif cmd == "7":
                filename = "output.pcm"
                #filename = input("文件路径:")
                await send_file_to_path(
                    "/rv1126b/voice",
                    filename
                )

            elif cmd == "q":
                print("服务器退出")
                import os
                os._exit(0)

        await asyncio.sleep(0.1)


async def main():
    server = await websockets.serve(
        register,
        "0.0.0.0",
        9001
    )
    print("WebSocket服务器启动")
    print("ws://0.0.0.0:9001")
    asyncio.create_task(process_command())

    await server.wait_closed()


if __name__ == "__main__":
    t = threading.Thread(
        target=keyboard_thread,
        daemon=True
    )
    t.start()
    asyncio.run(main())