# ECS 部署顺序

当前项目应部署到 ECS 的 `/opt/paro-web`，网站容器只绑定 `127.0.0.1:8000`，公网只由 Nginx 提供 `80/443`。

## 已有网站迁移公告

旧版使用仓库内的 `content/announcements.json`，新版使用不进入 Git 的 `data/announcements.json`。在 `git pull` 或上传新版代码之前，先在服务器执行一次，保留原有公告：

```bash
cd /opt/paro-web
sudo mkdir -p data
sudo cp -n content/announcements.json data/announcements.json
sudo chmod 644 data/announcements.json
```

然后更新代码，并执行 `sudo docker compose -f docker-compose.prod.yml up -d --build`。Compose 会从已有的 `/data` 挂载读取公告。之后直接通过 FinalShell 编辑 `/opt/paro-web/data/announcements.json`，保存后即可热更新，不需要再提交公告、拉取代码或重启容器。

新安装没有公告文件时会显示空公告列表；可参考 `content/announcements.example.json` 创建自己的公告文件。将最新公告放在数组第一项，每条新公告使用新的唯一 `id`。完整发布和校验方法见 [README](../README.md#公告发布与已读提醒)。

如果已经更新网站，却仍把新公告编辑在旧的 `content/announcements.json`，先校验旧文件内容：

```bash
cd /opt/paro-web
sudo docker compose -f docker-compose.prod.yml exec -T paro-web python -c "from pathlib import Path; from app.services.announcements import AnnouncementService; items = AnnouncementService(Path('/app/content/announcements.json')).load(); print('OK:', len(items)); print('Latest:', items[0].id if items else 'empty')"
```

只有输出 `OK` 且最新 ID 正确时，备份当前公告并将新内容复制到实际读取的位置：

```bash
sudo cp -a data/announcements.json "data/announcements.backup-$(date +%Y%m%d-%H%M%S).json"
sudo cp content/announcements.json data/announcements.json
sudo chmod 644 data/announcements.json
curl -fsS https://akitoya.top/api/v1/announcements
```

之后只编辑 `/opt/paro-web/data/announcements.json`。复制公告不需要重启容器。备份文件也由 Git 忽略。

页面的 CSS 和 JavaScript 链接会根据文件内容自动携带版本号；更新并重建容器后，刷新页面即可取得对应版本。首页和前端资源要求浏览器重新校验缓存，避免新 HTML 混用旧脚本或旧配色。若浏览器仍打开升级前的旧页面，首次可用 `Ctrl+F5` 强制刷新，无需清除 Cookie 或浏览器数据。

## 日常编辑公告与数据权限

容器以 UID/GID `10001:10001` 运行。不要为了编辑公告把数据库也改为登录用户所有，否则会出现 `attempt to write a readonly database`，导致页面初始化、抽取和导出失败。

如果使用 `akitoya` 账号通过 FinalShell 编辑公告，可按下面的权限分工：目录由 `akitoya` 拥有并允许容器组写入，数据库归容器，公告归 `akitoya`。目录的 setgid 位让新建文件继承容器组。

```bash
cd /opt/paro-web
sudo chown akitoya:10001 data
sudo chmod 2770 data
sudo chown 10001:10001 data/paro_web.sqlite3*
sudo chmod 660 data/paro_web.sqlite3*
sudo chown akitoya:10001 data/announcements.json
sudo chmod 640 data/announcements.json
```

这些命令也可用于修复误执行 `chown -R akitoya /opt/paro-web` 后的数据库权限，不修改记录内容。之后直接编辑 `data/announcements.json`；用临时文件替换公告时，保留组 `10001` 的读取权限。

## 上线初期异常记录清理

维护命令默认只读，口径为：`2026-09-24T00:00:00Z` 至 `2026-09-27T00:00:00Z` 内，同一匿名访客固定同一方、同一名称，任一 UTC 整点小时达到 300 条结果。它只处理固定模式，不会处理双方随机记录。

先拉取代码并构建新镜像，但暂时不要替换运行中的网站容器。然后用新镜像启动一次性容器预览；预览只读，不会修改数据库，网站可以继续运行：

```bash
cd /opt/paro-web
sudo git pull --ff-only origin master
sudo docker compose -f docker-compose.prod.yml build paro-web
sudo docker compose -f docker-compose.prod.yml run --rm --no-deps paro-web \
  python -m app.maintenance.cleanup_launch_abuse
```

预览会按组合展示完整匿名访客 ID、固定方、固定派生名称、北京时间范围、抽取批次数、结果条数和单小时峰值，并按北京时间日期汇总。确认明细无误后，停止网站写入，再用一次性容器执行。命令会先创建完整 SQLite 备份和 JSON 审计清单，之后才在单个事务中删除命中的 `draw_batches`；`draw_results` 由外键级联删除，`visitors` 和 Cookie 映射会保留。

```bash
sudo docker compose -f docker-compose.prod.yml stop paro-web
sudo docker compose -f docker-compose.prod.yml run --rm --no-deps paro-web \
  python -m app.maintenance.cleanup_launch_abuse \
  --apply --confirm DELETE_FLAGGED_BATCHES
sudo docker compose -f docker-compose.prod.yml up -d paro-web
```

备份和清单默认写在数据库旁，文件名带 UTC 时间戳。应用重启后全站统计缓存已清空；用户刷新个人记录页时会按原 Cookie 从服务器读取清理后的记录。若预览口径需要调整，可显式传入 `--start`、`--end` 或 `--peak-results-per-hour`，调整后必须重新预览再执行。

## 1. 上传项目

使用 FinalShell 文件管理器将整个项目目录上传到服务器临时目录，例如 `/home/akitoya/paro-web-upload`。不要上传 `.env`、`data/`、`.pytest_cache` 或任何密钥文件，以免覆盖服务器的数据库和公告。

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
