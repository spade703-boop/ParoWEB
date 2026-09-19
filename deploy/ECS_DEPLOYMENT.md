# ECS 部署顺序

当前项目应部署到 ECS 的 `/opt/paro-web`，网站容器只绑定 `127.0.0.1:8000`，公网只由 Nginx 提供 `80/443`。

## 1. 上传项目

使用 FinalShell 文件管理器将整个项目目录上传到服务器临时目录，例如 `/home/akitoya/paro-web-upload`。不要上传 `.env`、`data/*.sqlite3`、`.pytest_cache` 或任何密钥文件。

## 2. 放置项目

```bash
sudo mkdir -p /opt/paro-web
sudo cp -a /home/akitoya/paro-web-upload/. /opt/paro-web/
sudo mkdir -p /opt/paro-web/data
sudo chown -R root:root /opt/paro-web
sudo chown 10001:10001 /opt/paro-web/data
```

## 3. 创建生产环境文件

```bash
cd /opt/paro-web
sudo cp deploy/.env.production.example .env
sudo chmod 600 .env
```

确认 `.env` 中的域名是 `akitoya.top,www.akitoya.top`，不要把令牌、密码或 AccessKey 写入文件。

## 4. 构建并启动应用

```bash
cd /opt/paro-web
sudo docker compose -f docker-compose.prod.yml config --quiet
sudo docker compose -f docker-compose.prod.yml build
sudo docker compose -f docker-compose.prod.yml up -d
```

验证：

```bash
sudo docker compose -f docker-compose.prod.yml ps
curl -fsS http://127.0.0.1:8000/healthz
sudo ss -lntp | grep ':8000'
```

预期 `8000` 只显示 `127.0.0.1`，不能显示 `0.0.0.0` 或 `[::]`。

## 5. 安装 Nginx

```bash
sudo dnf install -y nginx
sudo mkdir -p /var/www/certbot
sudo cp deploy/nginx/akitoya.top.http.conf /etc/nginx/conf.d/akitoya.top.conf
sudo nginx -t
sudo systemctl enable --now nginx
```

备案完成、DNS 已指向 ECS 后，先访问 `http://akitoya.top` 验证反向代理，再申请证书。证书申请前不要启用 HTTPS 模板。

## 6. HTTPS

优先使用阿里云免费 DV 证书或 Let’s Encrypt。证书签发成功后，将 `akitoya.top.https.conf` 放入 `/etc/nginx/conf.d/akitoya.top.conf`，执行：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

确认 `https://akitoya.top` 正常后，再验证 HTTP 是否 301 到 HTTPS。

## 7. 不应执行

- 不要将 Compose 的 `8000` 改为公网绑定。
- 不要把网站加入 `mybot`、NapCat 或 Hermes 的容器网络和挂载目录。
- 不要执行 `docker system prune -a` 或 `docker volume prune`。
- 不要在项目目录保存 `.env` 到 Git 或上传聊天窗口。
