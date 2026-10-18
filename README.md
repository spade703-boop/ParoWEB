# 抽派生 · PARO DRAW

> 粉丝向娱乐小站：从「彰人池」「冬弥池」各抽一个派生组成配对，还有小概率抽到彩蛋。

非盈利同人向项目，网页端独立于机器人进程运行，数据各自隔离。

---

## 功能一览

| 功能 | 说明 |
| --- | --- |
| 抽派生 | 每次 1–3 连抽；可固定一方（指定彰人 / 冬弥的某个派生） |
| 彩蛋 | 3% 狐兔类特殊结果（狐狸、兔子、狐兔、狐兔饭、松饼、曲奇），3% 做饭 |
| 派生池 | 浏览全部条目（彰人 33 / 冬弥 32）+ 6 张特殊卡片，支持搜索、筛选、分页 |
| 趣味数据 | 全站彰人 / 冬弥 / 组合 TOP 10 |
| 我的记录 | 个人历史、做饭次数统计，可一键导出 PNG |
| 更新公告 | 改一个 JSON 文件即可发布，未读时导航显示 NEW |

## 技术栈

| 层 | 选型 |
| --- | --- |
| 后端 | FastAPI + aiosqlite（SQLite WAL），Python 3.11+ |
| 前端 | 原生 HTML / CSS / JS，无构建步骤 |
| 图片导出 | html2canvas 1.4.1 + 自托管 Noto Sans SC（浏览器内生成，2× 分辨率） |
| 部署 | Docker（`read_only` + 非 root UID 10001）+ Nginx |
| 测试 | `pytest`（后端）+ `node --test`（公告解析） |

## 快速开始

```powershell
python -m pip install -e ".[test]"
python -m uvicorn app.main:app --reload
```

打开 `http://127.0.0.1:8000`。

```powershell
python -m pytest
node --test
```

⚠️ 本地调试三件事：端口固定 8000（来源白名单只认它）；连抽前设 `PARO_DRAW_COOLDOWN_SECONDS=0`；
**不要双击 `index.html`**（`file://` 下 CSP 与 fetch 全部失效，页面会静默失败）。

## 目录结构

```
paro_web/
├─ app/
│  ├─ main.py           # 应用装配、安全响应头、CSP、静态资源版本化
│  ├─ config.py         # 环境变量 → Settings
│  ├─ domain/           # 抽取逻辑与领域模型（纯函数，无 IO）
│  ├─ services/         # 目录快照与热重载、抽取服务、公告
│  ├─ repositories/     # SQLite 读写（WAL）
│  ├─ web/              # API 路由、会话 Cookie、限流、错误处理
│  └─ static/           # 前端页面 + 导出用的字体与库
├─ content/             # 本地内容副本：两个 JSON + 头像素材（素材不入库）
├─ data/                # SQLite 与公告（Git 忽略）
├─ deploy/              # Nginx 配置与 ECS 部署步骤
└─ tests/               # pytest + node --test
```

## 接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/v1/session` | 建立匿名访客 Cookie |
| GET | `/api/v1/catalog` | 派生池、彩蛋概率、素材 URL |
| POST | `/api/v1/draw` | 抽派生 |
| GET | `/api/v1/me` | 个人记录（分页） |
| GET | `/api/v1/me/report` | 个人总览统计 |
| GET | `/api/v1/me/stats` | 个人做饭次数等汇总 |
| DELETE | `/api/v1/me/history` | 清空个人记录 |
| GET | `/api/v1/community-stats` | 全站趣味数据 |
| GET | `/api/v1/announcements` | 公告列表 |
| GET | `/healthz` | 健康检查（内容 + 数据库） |

## 配置

环境变量样例见 `.env.example`。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `PARO_ENV` | `development` | `production` 时关闭 `/docs` |
| `PARO_DATABASE_PATH` | `data/paro_web.sqlite3` | 与 Bot 统计数据物理隔离 |
| `PARO_CONTENT_DIR` | `content` | 条目 JSON 目录（生产 `/bot-data/paro`） |
| `PARO_IMAGES_DIR` | `<CONTENT_DIR>/images` | 头像目录（生产 `/bot-data/images`） |
| `PARO_ANNOUNCEMENTS_PATH` | `data/announcements.json` | 公告文件 |
| `PARO_COOKIE_SECURE` | 随环境 | HTTPS 站点必须为 `true` |
| `PARO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | 域名白名单 |
| `PARO_ALLOWED_ORIGINS` | `http://localhost:8000,…` | 写操作来源白名单 |
| `PARO_DRAW_COOLDOWN_SECONDS` | `5` | 起始冷却，`0` 关闭 |
| `PARO_STATS_CACHE_SECONDS` | `60` | 社区统计缓存秒数，`0` 关闭 |
| `PARO_BOT_DATA_DIR` | `/akito_bot/data` | Compose 挂载的 Bot 数据目录 |

冷却按**当日（中国标准时间）累计结果条数**分档：`<100→5s`、`100→10s`、`200→20s`、`300→30s`、
`400→45s`、`500→60s`、`700→120s`、`≥900→300s`。计数存 SQLite，重启容器不会绕过。

## 部署

Docker Compose 直接起，Bot 数据目录以只读方式挂载：

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

完整上线步骤（Nginx、HTTPS、公告迁移、权限）见 [`deploy/ECS_DEPLOYMENT.md`](deploy/ECS_DEPLOYMENT.md)。

上线前仍需搞定：备案、域名与 HTTPS，以及素材使用权确认。

## 数据与素材

内容与 Bot **共用同一份**，网站只读不动：

- 生产真源 = Bot 的 data 目录（`PARO_BOT_DATA_DIR`）；仓库内 `content/` 只是本地副本，改它对线上无效。
- `paro_pools.json`（派生条目）、`paro_config.json`（彩蛋概率）放在 `data/paro/`；头像放在
  `data/images/paro_avatars/{彰人,冬弥,fox&rabbit}/`。
- 改 JSON 或换头像后**无需重启**：网站按文件签名热重载，图片 URL 自带 `?v=<修改时间>`，访客不用清缓存。
- ⚠️ Bot 侧两个 JSON 是内存缓存，改名或加减派生需要超级用户发一次「重载配置 数据」；**只换图不用发**。
- ⚠️ 共用图片一律存 `.png` / `.jpg` / `.jpeg`（Bot 不认 `.webp`）；文件需对 UID 10001 可读（`install -m 644`）。
- 缺图时前端走文字占位，不会开天窗。

<details>
<summary>彩蛋配置怎么写（细节）</summary>

`paro_config.json` 的 `egg_categories`：每个大类给 `id` / `rate` / `priority` / `result_type` / `outcomes`。
当前 `foxrabbit` 3%（priority 100）+ `cooking` 3%（priority 0）。

- 一条抽取**只取一次随机数**，按 `priority` 降序累加 `rate` 划互斥区间 —— 不要拆成两次判定。
- `rate` 是占单次抽取的绝对比例，两类相加就是 6%。
- `result_type=special` 在 `outcomes` 内按 `weight` 加权选一个；`cooking` 直接产出普通配对做饭。
- `counts_as_cooking` 只影响统计与展示，不参与概率（只有狐兔饭是 `true`）。
- `unique_per_draw: true` 表示同一次抽取内该结果最多出现一次，实现是摇之前先剔除已命中的唯一分支。
- 约束：大类 `id` 唯一、结果 `id` 跨大类全局唯一、`weight` 为正数、`rate` 之和 ≤ 1、`priority` 为整数。
- 配错了不会崩：热重载失败时保留上一份有效快照，日志打 `Content reload failed`。

多图轮换：主图 `狐&兔.png` + 变体 `狐&兔_2.png`（下划线后必须是纯数字）。轮换按 `assets` 每个槽位各轮各的，
抽中那张**存进记录**、历史回看永远是当时那张；统计汇总与导出固定用主图。

</details>

<details>
<summary>公告与图片导出（细节）</summary>

**公告**：编辑 `data/announcements.json`（JSON 数组，最新放第一项，新公告使用新的唯一 `id`）。
必填 `published_at` / `title` / `summary` / 至少一条 `details`。已读状态存在浏览器本地，
修改最新公告的日期、标题、摘要、详情或标签，以及更换 `id`，都会重新提示。草稿先另存
`announcements.draft.json`，校验通过再替换正式文件。

**导出**：「个人总览导出」出一张完整统计图（不用勾选条目，列表加载失败也能导）；
「选择导出」最多 12 条具体记录。都在浏览器内生成 PNG，可预览、保存或调用设备分享。

</details>

## 后续规划

- 新增特殊彩蛋：狗狐
- 新增特殊彩蛋：派生随机变成狐 / 兔形态

## 说明

粉丝向非盈利娱乐功能，与官方无关。公开仓库不含头像素材，部署自有素材前请确认使用权。
