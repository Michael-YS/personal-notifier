# Agent 使用 Personal Notify

这份文档用于让 agent 调用用户自己的 Personal Notify 服务。使用前由用户提供服务地址和发送密钥。

## 调用配置

| 项目 | 值 |
| --- | --- |
| 服务地址 | `https://notify.example.com` |
| 发送接口 | `POST /notify` |
| 示例设备名 | `my-phone`（实际名称由用户提供） |
| 鉴权 | `Authorization: Bearer <NOTIFY_SEND_KEY>` |
| 健康检查 | `GET /healthz`，无需鉴权 |

调用方只需要发送密钥，不需要手机绑定密钥、FCM token 或 Firebase 服务账号私钥。发送密钥可以向所有已配置设备发送通知。

自建服务的发送密钥可保存在根目录 `.env` 的 `NOTIFY_SEND_KEY` 中。只在获得用户授权的环境中读取。其他机器通过环境变量或该机器的秘密管理工具配置 `NOTIFY_SEND_KEY`。

不要把实际密钥写入代码、Markdown、Git、命令行参数、聊天或日志；不要打印 `.env` 或完整请求头。手机使用的是绑定密钥，不能用来调用发送接口。

## 请求和响应

```json
{
  "title": "任务完成",
  "body": "测试通过，结果已保存。",
  "source": "coding-agent",
  "urgent": true,
  "ttl_seconds": 3600
}
```

- `title`、`body` 必填。省略 `device`（或传 `null`）默认广播给本次请求开始时所有已注册 FCM token 的设备，仅当 `urgent: true` 时向已配置的 Alexa 通道发送一次通知；尚未完成注册或已清理失效 token 的设备跳过。指定 `"device": "my-phone"` 则只发送给该手机，不发 Alexa。空字符串不是广播，会返回 422。
- `title` 为 1–200 字符，`body` 为 1–2000 字符，`source` 最多 100 字符，默认空字符串。服务还限制内部 data 消息的 UTF-8 JSON 大小为 3500 字节；中文和 emoji 占多个字节，正文应简短。
- `urgent` 默认为 `true`，使用 FCM high priority；`false` 使用 normal priority。广播时只有 `true` 才同步发送 Alexa，`false` 只走 FCM；省略 `urgent` 等同于 `true`。只为值得提醒用户的事件发送通知，避免循环刷屏。
- `ttl_seconds` 默认 3600，范围 0–2419200。过期的离线通知可能被丢弃；0 表示不能立即投递就丢弃。
- 未定义的字段会被拒绝。当前不支持附件、定时发送或调用方指定消息 ID。

指定单设备时的 HTTP 200 示例（兼容原接口）：

```json
{
  "id": "服务端生成的 UUID",
  "status": "accepted",
  "fcm_message_id": "projects/.../messages/..."
}
```

广播响应示例：

```json
{
  "id": "服务端生成的 UUID",
  "mode": "broadcast",
  "status": "partial",
  "accepted_count": 1,
  "failed_count": 1,
  "results": [
    {"device": "my-phone", "status": "accepted", "fcm_message_id": "projects/.../messages/..."},
    {"device": "my-tablet", "status": "failed", "error": "registration_expired"}
  ]
}
```

广播 HTTP 200 表示已处理各目标的发送尝试，必须检查响应 `status`：`accepted` 全部被上游接受，`partial` 部分成功，`failed` 全部未确认成功。`accepted_count`、`failed_count` 包含手机和 Alexa 通道。手机失败项的 `error` 为 `registration_expired` 或 `fcm_error`；失效 token 会清理，但扫码配对凭证保留。没有已注册手机且本次请求不发送 Alexa 时返回 409（包括普通优先级请求，即使已配置 Alexa）。广播使用相同通知 ID，返回每个设备名的结果；多个设备名登记了同一 App token 时，本地历史仍按通知 ID 去重。

Alexa 的结果没有 `device` 字段，使用 `channel: alexa`，例如 `{"channel":"alexa","status":"accepted"}`。失败时 `error` 为 `alexa_rate_limited`、`alexa_rejected`、`alexa_timeout` 或 `alexa_network_error`，上游 HTTP 拒绝还包含 `http_status`。超时的实际投递结果可能未知，不能自动重发。调用方不需要 Alexa access code；它只保存在服务器，只通过服务发送密钥调用 `/notify`。

上游接受发送不代表手机确认收到或 Echo 已亮灯。按结果向用户报告接受数量和失败目标，只有用户确认后才能报告已收到。`/healthz` 只检查服务进程，不检查 FCM、Alexa、设备绑定或手机在线状态。

## Python 调用示例（标准库）

将以下代码保存为调用方脚本，例如 `send_notify.py`。环境变量 `NOTIFY_URL` 和 `NOTIFY_SEND_KEY` 必须已配置，脚本不会输出密钥或原始异常。

```python
import json
import os
import urllib.error
import urllib.request

payload = {
    "title": "任务完成",
    "body": "测试通过，结果已保存。",
    "source": "coding-agent",
    "ttl_seconds": 3600,
}
request = urllib.request.Request(
    os.environ["NOTIFY_URL"].rstrip("/") + "/notify",
    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    headers={
        "Authorization": "Bearer " + os.environ["NOTIFY_SEND_KEY"],
        "Content-Type": "application/json; charset=utf-8",
    },
    method="POST",
)
try:
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)
    print("发送状态：", result["status"], "消息 ID：", result["id"])
    print("上游接受数量：", result["accepted_count"], "失败数量：", result["failed_count"])
    for item in result["results"]:
        if item["status"] == "failed":
            print("失败目标：", item.get("device", item.get("channel")), item["error"])
    if result["status"] != "accepted":
        raise SystemExit(1)
except urllib.error.HTTPError as error:
    print("发送失败，HTTP", error.code)
    raise SystemExit(1)
except (urllib.error.URLError, TimeoutError):
    print("网络错误或超时，投递结果未知；未自动重试")
    raise SystemExit(1)
```

在本仓库根目录运行，可由 uv 将本地 `.env` 注入进程，无需把密钥复制到命令中：

```powershell
.\scripts\uv.ps1 run --env-file .env python send_notify.py
```

Linux/macOS 且已安装 uv：

```sh
uv run --env-file .env python send_notify.py
```

## 错误处理

| HTTP / 情况 | Agent 应对 |
| --- | --- |
| 401 | 检查发送密钥；不要改用绑定密钥或反复重试。 |
| 404 | 设备名未配置，核对 `device`。 |
| 409 | 指定设备尚未绑定/token 已失效，或广播没有任何已注册设备；请用户打开 App 注册。 |
| 413 | 请求超过 Nginx 请求体上限，缩短内容。 |
| 422 | 参数、长度或 UTF-8 总大小错误，修正请求。 |
| 429 | 限流，稍后再试，避免发送循环。 |
| 502 / 其他 5xx | 服务或上游异常，报告失败；必要时由获授权的运维 agent 检查服务。广播的逐设备上游失败在 HTTP 200 的 `results` 中报告，也必须检查。 |
| 网络错误或超时 | 结果可能未知，勿声称未投递或自动重复发送。 |

服务没有发送队列、客户端幂等键或收件回执。每次请求生成新 ID，超时后重发可能产生重复通知。App 仅对相同 ID 去重。广播部分失败时不要重发整个广播；失败手机确需重试则指定其设备名，避免其他手机和 Alexa 再次收到。当前 API 不提供 Alexa 单独重试接口，不能把 `alexa` 当设备名重试。广播最多并发 8 个上游请求，大量设备可能超过调用方或代理超时；超时后的结果仍可能未知。需要重试时说明重复风险并限制次数。

Alexa 使用 Thomptronics Notify Me Skill 的通知通道，目标是亮灯提示、由用户询问后播放，而非直接语音播报。其 [FAQ](https://www.thomptronics.com/about/notify-me/faq) 标明每 5 分钟最多 5 条，并保留第三方消息日志 30 天。避免高频进度通知；机密内容不要放进默认广播正文。FCM 的 `ttl_seconds`、优先级和消息 ID 不会传递给 Notify Me，Alexa 侧不共享手机的过期和去重机制。

## 多设备规则

每个设备名只保存一个 FCM token。同一个设备名和绑定密钥放到两台手机上，后注册的手机会覆盖前一台；之后自动刷新也可能互相覆盖，不能实现同时通知两台手机。

多台设备需要各自的设备名和独立绑定凭证，例如 `my-phone`、`my-tablet`。推荐由用户打开自己的服务地址（例如 `https://notify.example.com`），输入管理密码、填写新设备名并生成二维码，然后在 App 点击“扫码配对”。网页自动生成每台设备的独立凭证，无需修改服务端环境变量；agent 只需要用户提供的设备名和发送密钥，不需要管理密码。

手工绑定模式仍可使用：在服务端 `NOTIFY_DEVICE_KEYS` 配置每个设备，校验要求所有绑定密钥与发送密钥互不相同。配置修改后需重新创建容器以加载新的环境变量（仅 `restart` 不会加载修改后的 `.env`）：

```sh
cd /opt/personal-notify
docker compose up -d --build --force-recreate
```

不要由发送 agent 自行修改设备、轮换密钥或部署服务；在用户授权相应操作后再执行。向所有注册设备通知时省略 `device`；只想通知其中一台时指定它的设备名。
