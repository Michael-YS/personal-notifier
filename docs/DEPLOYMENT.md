# 部署与运维

## Docker Compose

从 `.env.example` 创建 `.env`，按需设置 `CHANNEL_FCM_ENABLED`、`CHANNEL_ALEXA_ENABLED` 和 `CHANNEL_EMAIL_ENABLED`，并准备启用渠道的凭据。然后在仓库根目录运行：

```sh
docker compose up -d --build
```

容器使用 UID 10001，服务仅绑定宿主机 `127.0.0.1:8000`。设备状态在命名卷 `notify-data` 中，`secrets/` 目录以只读方式挂载到 `/app/secrets`。Linux 上请确保容器用户可以进入目录并读取启用渠道的文件。启用 FCM 时，例如：

```sh
sudo chgrp 10001 secrets
sudo chmod 710 secrets
sudo chown 10001:10001 secrets/firebase-service-account.json
sudo chmod 400 secrets/firebase-service-account.json
chmod 600 .env
```

不要将私钥变成公开可读。备份设备卷和必要的凭据；丢失状态会丢失扫码设备的绑定凭证摘要。不要使用 `docker compose down -v`，除非确实要删除所有设备状态。

## HTTPS 与 Nginx

`deploy/nginx-http.conf` 和 `deploy/nginx.conf` 是模板。将其中的 `notify.example.com` 替换成自己的域名，并设置 DNS 指向服务器。

1. 创建 `/var/www/personal-notify-acme`，安装 HTTP 配置，使用 `nginx -t` 检查后重载。
2. 使用 Certbot 的 webroot 模式申请证书：

   ```sh
   sudo certbot certonly --webroot -w /var/www/personal-notify-acme -d notify.example.com
   ```

3. 确认证书路径存在，以及 Certbot 的 `options-ssl-nginx.conf`、`ssl-dhparams.pem` 已安装，再启用 HTTPS 配置。使用发行版 Certbot Nginx 集成也可以。
4. 启用证书续期，并将 `deploy/reload-nginx.sh` 安装为续期 deploy hook；确保脚本可执行，先验证续期配置。
5. 设置 `.env` 的 `NOTIFY_PUBLIC_URL=https://notify.example.com`，重新创建容器，然后验证 HTTPS `/healthz`、未鉴权 `/notify` 返回 401 和网页配对。

模板限制请求体为 16 KB，限制来源地址请求频率，关闭访问日志。如果使用 CDN 代理，来源地址可能是代理出口，应按自己的代理配置调整限流和可信来源设置。

## 可选 Alexa

在 `.env` 中设置 `CHANNEL_ALEXA_ENABLED=true` 和 `NOTIFY_ALEXA_CODE_FILE=secrets/alexa-access-code.txt`。准备凭据并让 UID 10001 可读：

```sh
sudo chown 10001:10001 secrets/alexa-access-code.txt
sudo chmod 400 secrets/alexa-access-code.txt

docker compose up -d --build
```

使用基础 Compose 就能加载该文件。已有的部署如果通过覆盖文件设置容器内路径，Compose 中的 `environment` 会优先于 `env_file`。

## GHCR 镜像

`.github/workflows/docker.yml` 使用 `GITHUB_TOKEN` 登录并发布到 `ghcr.io/<owner>/<repo>`，镜像路径自动转为小写。无需手工提供 registry 密码。

- `main` / `master` push：分支标签和 SHA 标签；默认分支还生成 `latest`。
- `v0.2.3` 等版本标签：生成 `0.2.3`、`0.2` 和 SHA 标签。
- PR：构建验证，不推送镜像。
- `workflow_dispatch`：可手动构建并发布选定分支或标签。

工作流默认构建 Linux amd64。GitHub Packages 可见性独立于仓库可见性：需要匿名拉取时，在首次成功发布后检查 package 的访问设置。私有镜像需要先用有 `read:packages` 权限的凭据登录。

使用已发布镜像时，创建自己的覆盖文件（修改实际 owner/repo/tag）：

```yaml
services:
  notify:
    image: ghcr.io/your-owner/your-repo:0.2.3
```

然后拉取并启动，避免触发本地构建：

```sh
docker compose -f compose.yaml -f compose.ghcr.yaml pull
docker compose -f compose.yaml -f compose.ghcr.yaml up -d --no-build --wait
```

需要健康检查或 Alexa 时，可将 `deploy/compose.production.yaml` 中对应配置合并进自己的覆盖文件。建议固定版本标签或 digest，避免 `latest` 引起意外升级。构建上下文仅允许 `server/`、`pyproject.toml` 和 `uv.lock`，不包含运行凭据、Android 配置、设备状态或开发工具。

## 更新、轮换与验证

```sh
docker compose ps
docker compose logs --tail=50 notify
docker compose up -d --build --force-recreate
```

采用覆盖配置时，上述操作也使用相同的 `-f` 文件组合。修改 `.env` 后必须重新创建容器；仅 `restart` 不会加载新的环境变量。Alexa code 文件在启动时读取，轮换后也需重新创建容器。

升级前备份状态卷，保留旧镜像。回滚时指定旧镜像，并核对其状态文件兼容性；不要删除卷来修复启动错误。`/healthz` 只证明进程在运行，仍需真实设备验证通知接收。
