# 抽派生网站

独立的响应式网站实现，不依赖 NoneBot，也不读写机器人统计文件。内容已从参考 Bot 复制到本项目并独立管理。

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

网站数据默认保存在 `data/paro_web.sqlite3`，与 Bot 数据物理隔离。正式公开前仍需确认素材使用权并完成备案、域名和 HTTPS 配置。

## 素材说明

公开仓库不包含 `content/images/` 下的头像素材。运行时缺少头像会使用文字占位；部署自有素材前请先确认使用权。
