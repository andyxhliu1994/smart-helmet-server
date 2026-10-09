name: helmet-device-protocol-debug
version: 1.0.0
description: 智能头盔设备协议联调说明。

http_upload:
- POST /api/upload
- form 字段名：file
- 文件名：{sn}-{timestamp}.pcm/jpeg/h264
- 上传成功响应：
  {
    "code": 0,
    "msg": "success",
    "data": {
      "sn": "rv1126b",
      "file": "rv1126b-123456789.pcm",
      "type": "pcm",
      "size": 11137776
    }
  }

websocket:
- /{sn}/cmd：上传通知、ack、voice_start、voice_end、voice_cancel
- /{sn}/voice：服务端下发裸 16k pcm 二进制分片

common_issues:
- cmd 与文件后缀不一致：按真实后缀处理并记录 warning。
- 上传通知已收到但找不到文件：返回 file_not_found。
- voice 未连接：Web 展示结果，语音不下发。
