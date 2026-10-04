# Personal Notify

给自己用的 Android 推送工具：Python HTTP API → Firebase Cloud Messaging → Kotlin App。通知历史保存在手机本地，服务端只维护一个设备 JSON 文件；可选同步到 Alexa。

## 功能与限制

- 原生 Kotlin Android App，Room 本地历史、已读状态、删除和扫码配对。
- FastAPI 服务端，无数据库、无服务端通知历史。
- 默认广播给所有已注册手机，也可以指定单台设备。
- 高优先级广播同步到已配置的 Alexa；普通优先级只走 FCM。
- 每台设备使用独立绑定凭证，发送密钥与配对管理密码独立。

这是个人使用的轻量服务。必须使用**单进程、单 worker、单实例**，不能让多个进程共享状态文件。没有发送队列、收件回执或调用方幂等键，FCM 和 Alexa 都不保证即时或必达；服务返回 `accepted` 表示上游接受，并不代表设备收到。

## 目录

| 路径 | 内容 |
| --- | --- |
| `server/` | API、JSON 设备状态、二维码配对、Alexa 通道及网页 |
| `android/` | Android App、Room schema 和仪器测试 |
| `tests/` | 使用假发送器的服务端测试 |
| `deploy/` | Docker Compose 与 Nginx 示例 |
| [AGENT_NOTIFY.md](AGENT_NOTIFY.md) | 发通知的 agent 接入指南 |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | HTTPS 部署、GHCR 镜像和运维 |

## 1. 准备 Firebase

1. 创建自己的 Firebase 项目，启用 Firebase Cloud Messaging HTTP v1 API。
2. 注册 Android 应用，包名为 `dev.personalnotify`。
3. 下载客户端 `google-services.json`，放到 `android/app/google-services.json`。
4. 为服务端创建具有 FCM 发送权限的服务账号，例如授予 `roles/firebasecloudmessaging.admin`；将 JSON 私钥保存到 `secrets/firebase-service-account.json`。

客户端配置和服务端私钥必须属于同一个 Firebase 项目。服务端私钥绝不能放到 APK 中。这两个配置文件均被 Git 忽略，每位使用者应准备自己的配置。

## 2. 本地 Python 服务（uv）

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)。在根目录运行：

```sh
uv sync --locked
```

将 `.env.example` 复制为 `.env`，创建 `secrets/` 并放入服务账号私钥。生成随机密钥时，在自己的终端运行：

```sh
uv run python -c "import secrets; print(secrets.token_urlsafe(32))"
```

分别生成发送密钥、每台静态设备的绑定密钥、配对管理密码，不要复用。

| 环境变量 | 用途 |
| --- | --- |
| `NOTIFY_SEND_KEY` | 发送密钥，至少 32 字符 |
| `NOTIFY_DEVICE_KEYS` | 非空 JSON 对象，设备名 → 独立绑定密钥，每个密钥至少 32 字符 |
| `NOTIFY_STATE_FILE` | 设备状态路径，默认 `data/devices.json` |
| `GOOGLE_APPLICATION_CREDENTIALS` | 服务账号 JSON 路径 |
| `NOTIFY_PUBLIC_URL` | 外部 HTTPS 根地址，扫码配对时必需 |
| `NOTIFY_PAIR_PASSWORD` | 独立的配对管理密码，至少 16 字符；留空禁用配对 |
| `NOTIFY_ALEXA_CODE_FILE` | 可选，Alexa access code 文件路径 |

`.env` 中的设备 JSON 应用单引号包裹，例如 `NOTIFY_DEVICE_KEYS='{"my-phone":"独立的随机密钥"}'`。设备名只能包含字母、数字、下划线和短横线，长度 1–64。即使主要使用扫码配对，当前配置也要求至少一个静态设备；未注册的静态设备不会参与广播。

```sh
uv run --env-file .env uvicorn server.app:create_app --factory --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

Windows 可将以上 `uv` 替换为 `.\scripts\uv.ps1`。脚本优先调用已安装的 uv，也支持项目内 `.tools/bin/uv.exe`。

手机连接需要 HTTPS。将反向代理指向 `127.0.0.1:8000`，参见[部署说明](docs/DEPLOYMENT.md)。App 不允许明文 HTTP，也不跟随绑定接口的重定向。

## 3. 构建 Android App

需要 JDK 17、Android SDK 35，最低支持 Android 8。可用 Android Studio 打开 `android/`，也可执行：

```sh
cd android
./gradlew assembleDebug
```

Windows 使用 `gradlew.bat`。APK 输出为 `android/app/build/outputs/apk/debug/app-debug.apk`。

安装到具备 Google Play 服务、能连接 FCM 网络的手机。首次打开授予通知权限，然后选择：

- **扫码配对**：打开自己的服务网页，输入管理密码和新的设备名，生成二维码；App 点击“扫码配对”，扫描并确认服务地址。
- **手工绑定**：填入 HTTPS 服务地址、`NOTIFY_DEVICE_KEYS` 中的设备名和对应绑定密钥。

状态显示“已绑定”后可发通知。每台手机使用不同设备名；同一设备名后注册的 token 会覆盖原值。二维码 5 分钟过期、仅可兑换一次，服务重启后未兑换二维码失效。扫码设备凭证摘要存于服务端，原始凭证由手机保存。网页目前只提供新增配对，不提供删除设备管理。

App 在打开、FCM token 更新时重新注册，并通过 WorkManager 每天刷新一次（系统调度，不保证准确时间）。历史保存在本机，列表显示最近 200 条，不自动上传、不启用系统备份。卸载或清空数据会丢失历史和绑定配置。长期使用请配置固定的 release 签名，签名改变后不能直接覆盖安装。

## 4. 发送通知

设置调用方环境变量 `NOTIFY_URL`（你的 HTTPS 根地址）和 `NOTIFY_SEND_KEY`：

```python
import json
import os
import urllib.request

request = urllib.request.Request(
    os.environ["NOTIFY_URL"].rstrip("/") + "/notify",
    data=json.dumps({
        "title": "备份完成",
        "body": "今晚的备份已上传",
        "source": "backup-server",
        "urgent": False,
    }, ensure_ascii=False).encode("utf-8"),
    headers={
        "Authorization": "Bearer " + os.environ["NOTIFY_SEND_KEY"],
        "Content-Type": "application/json; charset=utf-8",
    },
    method="POST",
)
with urllib.request.urlopen(request, timeout=30) as response:
    print(json.load(response))
```

| 请求 | 发送目标 |
| --- | --- |
| 省略 `device`，`urgent: true`（默认） | 所有已注册手机 + 已配置的 Alexa |
| 省略 `device`，`urgent: false` | 所有已注册手机，仅 FCM |
| 指定 `device` | 仅指定手机，优先级由 `urgent` 决定 |

`title` 为 1–200 字符、`body` 为 1–2000 字符、`source` 最多 100 字符。内部 data 消息还有 3500 字节 UTF-8 JSON 上限。`ttl_seconds` 默认 3600，范围 0–2419200；0 表示无法立即投递就丢弃。未知字段被拒绝。

广播返回 HTTP 200 后仍须检查 `status`：`accepted` 全部被上游接受、`partial` 部分成功、`failed` 全部失败。数量包含手机和 Alexa。没有可发送目标返回 409；仅配置 Alexa 且没有注册手机时，高优先级广播可走 Alexa，普通优先级仍返回 409。

完整响应、鉴权和错误处理见 [AGENT_NOTIFY.md](AGENT_NOTIFY.md)。`GET /healthz` 不需要鉴权，仅检查服务进程，不检查凭据、上游或设备状态。

## 可选：Alexa 亮灯提醒

启用 [Thomptronics Notify Me Skill](https://www.thomptronics.com/about/notify-me)，将获得的 access code 保存为 `secrets/alexa-access-code.txt`（单行，不加引号），设置 `NOTIFY_ALEXA_CODE_FILE` 指向它。文件在启动时读取，修改后需重新启动进程或重建容器。

该通道通过第三方服务发送，提示方式由 Skill 和关联 Amazon 账号决定。只有高优先级广播会调用它，指定手机不会调用。Alexa 不共享手机的 TTL 和消息 ID 去重机制；失败不自动重试。使用前查看供应商的[限额与隐私说明](https://www.thomptronics.com/about/notify-me/faq)，避免发送机密正文。

## 验证与 CI

```sh
uv run pytest -q
uv run ruff check server tests
uv run ruff format --check server tests
```

服务端测试不访问 Google 或 Alexa，覆盖鉴权、状态恢复、一次性配对、并发兑换、广播与优先级路由等。

无需 Firebase 配置的 Android 编译检查：

```sh
cd android
./gradlew -PbuildCheck=true assembleDebug assembleDebugAndroidTest lintDebug
```

这个模式使用 `.buildcheck` 包名后缀，不能接收真实推送。有设备或模拟器时，执行 `./gradlew -PbuildCheck=true connectedDebugAndroidTest` 验证本地历史和二维码解析。实际 FCM 验收需正常构建、自己的配置和手机，分别测试前台、后台和锁屏。

GitHub Actions 执行服务端检查及 Android build-check 构建/lint。Docker 工作流在主分支和 `v*` 标签上发布 GHCR 镜像，PR 只构建；详见[部署说明](docs/DEPLOYMENT.md#ghcr-镜像)。工作流不会上传 Firebase、Alexa 或签名凭据。

采用 FCM data 消息，App 在接收回调中写入本地历史并显示通知。无通知权限时仍可保存实际收到的消息；长期没有可见通知的 high priority 消息可能被 FCM 降级。断网、系统限制、强行停止 App 和 TTL 都可能导致延迟或丢失，本地历史不是可靠的服务端审计记录。

参考：[Android FCM 接入](https://firebase.google.com/docs/cloud-messaging/android/get-started)、[消息优先级](https://firebase.google.com/docs/cloud-messaging/android-message-priority)、[Admin SDK 发送](https://firebase.google.com/docs/cloud-messaging/send/admin-sdk)。
