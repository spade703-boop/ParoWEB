# 抽派生网站

独立的响应式网站实现，不依赖 NoneBot 进程，也不读写机器人统计文件。生产环境通过只读挂载直接读取 Bot 的 `data` 目录，因此派生池、头像和特殊结果配置只维护一份；网页会在请求时检测内容变化并热重载。更新公告单独保存在网站的 `content/announcements.json`。

## 本地运行

需要 Python 3.11 或更高版本：

```powershell
python -m pip install -e ".[test]"
python -m uvicorn app.main:app --reload
```

然后访问 `http://127.0.0.1:8000`。

## 测试

```powershell
python -m pytest
```

## 配置

环境变量示例见 `.env.example`。生产环境必须设置：

- `PARO_ENV=production`
- `PARO_COOKIE_SECURE=true`
- 正确的 `PARO_ALLOWED_HOSTS`
- HTTPS 站点对应的 `PARO_ALLOWED_ORIGINS`
- 持久化路径 `PARO_DATABASE_PATH`
- Bot 数据源路径 `PARO_CONTENT_DIR`（生产 Compose 默认是容器内的 `/bot-data`）

生产 Compose 会将宿主机 `${PARO_BOT_DATA_DIR:-/akito_bot/data}` 以只读方式挂载到 `/bot-data`，并将网站仓库的 `content/` 以只读方式挂载到 `/app/content`。如果 Bot 数据目录不同，在服务器的 `.env` 中设置 `PARO_BOT_DATA_DIR=/实际路径/data`，再重建网页容器即可。之后 Bot 更新 `paro_pools.json`、`paro_config.json` 或头像文件时，网页不需要复制文件或重新构建镜像；下一次访问目录、抽取或打开页面时会自动读取新内容。更新 `content/announcements.json` 后也无需重新构建镜像，服务器执行 `git pull` 后公告服务会在下一次请求时自动热加载；只有修改 Python、HTML、CSS 或 JavaScript 等镜像内文件时才需要 `docker compose up -d --build`。

网站数据默认保存在 `data/paro_web.sqlite3`，与 Bot 统计数据物理隔离。正式公开前仍需确认素材使用权并完成备案、域名和 HTTPS 配置。

## 素材说明

公开仓库不包含 `content/images/` 下的头像素材。运行时缺少头像会使用文字占位；部署自有素材前请先确认使用权。
