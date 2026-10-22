from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
from datetime import UTC, datetime
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

import aiosqlite
import httpx
import pytest

from app.config import Settings
from app.domain.models import DrawResult
from app.main import create_app
from app.repositories.sqlite import SCHEMA, SQLiteRepository


_CONTENT = Path(__file__).resolve().parents[1] / "content"
#: 1x1 transparent PNG: the artwork itself is gitignored, so tests stub it.
_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _stub_content(root: Path) -> Path:
    """Copy the tracked content JSON and stand in for the gitignored artwork.

    The real ``content/images/paro_avatars`` tree is not committed, so a clean
    checkout would otherwise render empty asset lists.
    """
    content = root / "content"
    content.mkdir(parents=True, exist_ok=True)
    for name in ("paro_pools.json", "paro_config.json"):
        (content / name).write_bytes((_CONTENT / name).read_bytes())
    fox = content / "images" / "paro_avatars" / "fox&rabbit"
    fox.mkdir(parents=True, exist_ok=True)
    for stem in ("狐&兔", "狐&兔_2"):
        (fox / f"{stem}.png").write_bytes(_PIXEL_PNG)
    return content


@pytest.fixture
def settings(tmp_path) -> Settings:
    base = Settings.from_env()
    content = _stub_content(tmp_path)
    announcements_path = tmp_path / "announcements.json"
    announcements_path.write_text(
        json.dumps([
            {
                "id": "test-announcement",
                "published_at": "2026-10-15",
                "title": "测试公告",
                "summary": "测试摘要",
                "details": ["测试详情"],
                "tags": ["测试"],
                "highlight": True,
            }
        ], ensure_ascii=False),
        encoding="utf-8",
    )
    return replace(
        base,
        announcements_path=announcements_path,
        content_dir=content,
        images_dir=content / "images",
        database_path=tmp_path / "test.sqlite3",
        cookie_secure=False,
        allowed_hosts=("testserver",),
        allowed_origins=("http://testserver",),
        draw_cooldown_seconds=0,
    )


@pytest.fixture
async def client(settings: Settings):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as api_client:
            yield api_client


async def test_session_catalog_draw_profile_and_clear(client: httpx.AsyncClient) -> None:
    session = await client.post("/api/v1/session", json={}, headers={"Origin": "http://testserver"})
    assert session.status_code == 200
    assert "paro_visitor=" in session.headers["set-cookie"]
    assert "HttpOnly" in session.headers["set-cookie"]

    catalog = await client.get("/api/v1/catalog")
    assert catalog.status_code == 200
    catalog_payload = catalog.json()
    assert catalog_payload["akito"]
    assert [category["id"] for category in catalog_payload["egg_categories"]] == [
        "foxrabbit",
        "cooking",
    ]
    assert [category["rate"] for category in catalog_payload["egg_categories"]] == [0.03, 0.03]
    assert catalog_payload["egg_categories"][0]["outcomes"] == [
        "fox",
        "rabbit",
        "foxrabbit",
        "foxbun",
        "pancake",
        "cookie",
    ]
    special_outcomes = {
        outcome["id"]: outcome for outcome in catalog_payload["special_outcomes"]
    }
    assert special_outcomes["fox"]["category_id"] == "foxrabbit"
    assert special_outcomes["foxbun"]["counts_as_cooking"] is True
    # 松饼 / 曲奇 是过路彩蛋：不计入做饭，文案带硬换行，前端按行渲染
    assert special_outcomes["pancake"]["counts_as_cooking"] is False
    assert special_outcomes["cookie"]["counts_as_cooking"] is False
    # 派生池靠 variant_urls 做相册堆叠：按 assets 槽位分组，一组一张到多张。
    # 这里只钉结构（素材被 .gitignore 排除，数量取决于本地有没有图）。
    for outcome in special_outcomes.values():
        groups = outcome["variant_urls"]
        assert isinstance(groups, list)
        assert all(isinstance(group, list) for group in groups)
        assert len(groups) == len(outcome["asset_urls"])
    # 狐兔是「狐+兔」两个槽位，各一张 → 不会被误判成可堆叠
    assert all(len(group) == 1 for group in special_outcomes["foxrabbit"]["variant_urls"])
    assert "\n" in special_outcomes["pancake"]["message"]
    assert "\n" in special_outcomes["cookie"]["message"]
    fixed_name = catalog_payload["akito"][0]["name"]

    draw = await client.post(
        "/api/v1/draw",
        json={"count": 3, "fixed_side": "akito", "fixed_name": fixed_name},
        headers={"Origin": "http://testserver"},
    )
    assert draw.status_code == 200
    draw_results = draw.json()["results"]
    assert len(draw_results) == 3
    for result in draw_results:
        if result["special_type"]:
            assert result["category_id"] == special_outcomes[result["special_type"]]["category_id"]
        elif result["is_cooking"]:
            assert result["category_id"] == "cooking"
        else:
            assert result["category_id"] is None

    profile = await client.get("/api/v1/me")
    assert profile.json()["draw_count"] == 3
    assert len(profile.json()["recent"]) == 3

    cleared = await client.delete("/api/v1/me/history", headers={"Origin": "http://testserver"})
    assert cleared.status_code == 200
    assert (await client.get("/api/v1/me")).json()["draw_count"] == 0


async def test_validation_origin_and_content_type(client: httpx.AsyncClient) -> None:
    invalid = await client.post(
        "/api/v1/draw",
        json={"count": 4, "fixed_side": "none", "fixed_name": None},
        headers={"Origin": "http://testserver"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_request"

    wrong_origin = await client.post(
        "/api/v1/draw",
        json={"count": 1, "fixed_side": "none", "fixed_name": None},
        headers={"Origin": "https://example.com"},
    )
    assert wrong_origin.status_code == 403

    wrong_type = await client.post(
        "/api/v1/draw",
        content="{}",
        headers={"Origin": "http://testserver", "Content-Type": "text/plain"},
    )
    assert wrong_type.status_code == 415


async def test_health_and_database_does_not_store_raw_cookie(client: httpx.AsyncClient, settings: Settings) -> None:
    response = await client.post("/api/v1/session", json={}, headers={"Origin": "http://testserver"})
    raw_token = response.cookies.get("paro_visitor")
    database_bytes = settings.database_path.read_bytes()
    assert raw_token.encode() not in database_bytes
    assert (await client.get("/healthz")).status_code == 200


async def test_homepage_exposes_image_export_and_blob_csp(client: httpx.AsyncClient) -> None:
    homepage = await client.get("/")
    assert homepage.status_code == 200
    assert homepage.headers["cache-control"] == "no-cache"
    assert "/static/export-image.js" in homepage.text
    assert "img-src 'self' data: blob:" in homepage.headers["content-security-policy"]
    assert "'unsafe-inline'" not in homepage.headers["content-security-policy"]
    asset_urls = re.findall(r'/static/[^"?]+\.(?:css|js)\?v=[0-9a-f]{16}', homepage.text)
    assert len(asset_urls) == 5
    for asset_url in asset_urls:
        asset = await client.get(asset_url)
        assert asset.status_code == 200
        assert asset.headers["cache-control"] == "no-cache"

    exporter = await client.get("/static/export-image.js")
    assert exporter.status_code == 200
    assert exporter.headers["cache-control"] == "no-cache"
    assert "ParoImageExporter" in exporter.text


@pytest.mark.parametrize("filename", ["app.css", "app.js", "export-image.js", "profile-export.css", "profile-export.js"])
async def test_changed_frontend_asset_gets_a_new_url(settings: Settings, tmp_path, filename: str) -> None:
    project_root = tmp_path / "site"
    static_dir = project_root / "app" / "static"
    static_dir.mkdir(parents=True)
    for asset_name in ("index.html", "app.css", "app.js", "export-image.js", "profile-export.css", "profile-export.js"):
        source = settings.project_root / "app" / "static" / asset_name
        (static_dir / asset_name).write_bytes(source.read_bytes())
    application = create_app(replace(settings, project_root=project_root))
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as api_client:
        previous = await api_client.get("/")
        previous_url = re.search(rf'/static/{re.escape(filename)}\?v=[0-9a-f]{{16}}', previous.text).group()
        asset_path = static_dir / filename
        updated_bytes = asset_path.read_bytes() + b"\n"
        asset_path.write_bytes(updated_bytes)
        current = await api_client.get("/")
        current_url = re.search(rf'/static/{re.escape(filename)}\?v=[0-9a-f]{{16}}', current.text).group()
        assert current_url != previous_url
        assert (await api_client.get(current_url)).content == updated_bytes


async def test_announcements_are_available(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/announcements")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["version"]
    assert payload["items"]
    assert {"id", "published_at", "title", "summary", "details", "tags", "highlight"} <= set(payload["items"][0])


async def test_announcements_reload_after_file_replacement(client: httpx.AsyncClient, settings: Settings) -> None:
    previous = (await client.get("/api/v1/announcements")).json()
    latest = {**previous["items"][0], "id": "new-announcement", "title": "热更新公告"}
    items = [latest, *previous["items"]]
    draft_path = settings.announcements_path.with_name("announcements.draft.json")
    draft_path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    draft_path.replace(settings.announcements_path)

    response = await client.get("/api/v1/announcements")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["version"] != previous["version"]
    assert response.json()["items"] == items


async def test_invalid_announcement_edit_keeps_last_valid_content(client: httpx.AsyncClient, settings: Settings) -> None:
    previous = (await client.get("/api/v1/announcements")).json()
    settings.announcements_path.write_text('[{"id":', encoding="utf-8")

    response = await client.get("/api/v1/announcements")
    assert response.status_code == 200
    assert response.json() == previous

    repaired = [{**previous["items"][0], "id": "repaired-announcement"}]
    settings.announcements_path.write_text(json.dumps(repaired), encoding="utf-8")
    assert (await client.get("/api/v1/announcements")).json()["items"] == repaired


async def test_community_stats_reuses_one_computation(client: httpx.AsyncClient) -> None:
    """缓存期内复用同一份结果（generated_at 不动）；抽卡让它失效后必须重算。"""
    first = (await client.get("/api/v1/community-stats")).json()
    second = (await client.get("/api/v1/community-stats")).json()
    assert first["total_draws"] == 0
    assert second["generated_at"] == first["generated_at"]

    catalog = (await client.get("/api/v1/catalog")).json()
    draw = await client.post(
        "/api/v1/draw",
        json={"count": 1, "fixed_side": "akito", "fixed_name": catalog["akito"][0]["name"]},
        headers={"Origin": "http://testserver"},
    )
    assert draw.status_code == 200

    third = (await client.get("/api/v1/community-stats")).json()
    assert third["total_draws"] == 1
    assert third["generated_at"] > first["generated_at"]


async def test_cooking_pair_top_ignores_fixed_and_foxbun(settings: Settings) -> None:
    """做饭组合榜只算双方随机 + 普通做饭；单人纪录则按含狐兔饭口径取最大值。"""
    repository = SQLiteRepository(settings.database_path)
    await repository.initialize()
    stamp = "2026-10-18T00:00:00Z"

    async with repository.connect() as connection:
        await connection.execute("BEGIN IMMEDIATE")
        for visitor_id in ("v1", "v2"):
            await connection.execute(
                "INSERT INTO visitors(id, token_hash, created_at, last_seen_at) VALUES (?, ?, ?, ?)",
                (visitor_id, f"hash-{visitor_id}", stamp, stamp),
            )

        def batch(batch_id: str, visitor_id: str, fixed_side: str) -> list:
            return [
                "INSERT INTO draw_batches(id, visitor_id, requested_count, fixed_side, fixed_name, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (batch_id, visitor_id, 3, fixed_side, None, stamp),
            ]

        sql, params = batch("b1", "v1", "none")
        await connection.execute(sql, params)
        sql, params = batch("b2", "v2", "akito")
        await connection.execute(sql, params)

        results = [
            # 随机模式下的两次普通做饭：这一对应当成为榜首
            ("r1", "b1", 1, "黑百合", "王子冬", 1, None, None, 1),
            ("r2", "b1", 2, "黑百合", "王子冬", 1, None, None, 1),
            # 狐兔饭：counts_as_cooking 但 is_cooking=0，不进组合榜，只进单人纪录
            ("r3", "b1", 3, None, None, 0, "foxbun", None, 1),
            # 固定模式下的普通做饭：不进全站组合榜（全站榜只统计双方随机）
            ("r4", "b2", 1, "黑百合", "堵车冬", 1, None, None, 1),
        ]
        for row in results:
            await connection.execute(
                """
                INSERT INTO draw_results(
                    id, batch_id, position, akito_name, toya_name, is_cooking,
                    special_type, special_asset, counts_as_cooking, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (*row, stamp),
            )
        await connection.commit()

    stats = await repository.community_stats(limit=10)
    assert stats["cooking_pair_top"] == [
        {"akito_name": "黑百合", "toya_name": "王子冬", "count": 2}
    ]
    # v1 = 2 次普通做饭 + 1 次狐兔饭 = 3；v2 = 1
    assert stats["record_holders"]["cooking"] == 3
    assert stats["record_holders"]["specials"] == {"foxbun": 1}


async def test_community_stats_are_aggregated(client: httpx.AsyncClient) -> None:
    empty = await client.get("/api/v1/community-stats")
    assert empty.status_code == 200
    assert empty.headers["cache-control"] == "no-store"
    payload = empty.json()
    # generated_at 是这份数据算出来的时刻，每次都不一样，单独断言
    generated_at = payload.pop("generated_at")
    assert payload == {
        "total_draws": 0,
        "random_draws": 0,
        "akito_top": [],
        "toya_top": [],
        "pair_top": [],
        "cooking_pair_top": [],
        "record_holders": {"cooking": 0, "specials": {}},
    }
    assert generated_at

    catalog = (await client.get("/api/v1/catalog")).json()
    fixed_name = catalog["akito"][0]["name"]
    draw = await client.post(
        "/api/v1/draw",
        json={"count": 3, "fixed_side": "akito", "fixed_name": fixed_name},
        headers={"Origin": "http://testserver"},
    )
    assert draw.status_code == 200

    stats = (await client.get("/api/v1/community-stats")).json()
    assert stats["total_draws"] == 3
    assert stats["random_draws"] == 0
    assert stats["akito_top"] == []
    assert stats["toya_top"] == []

    random_draw = await client.post(
        "/api/v1/draw",
        json={"count": 3, "fixed_side": "none", "fixed_name": None},
        headers={"Origin": "http://testserver"},
    )
    assert random_draw.status_code == 200
    stats = (await client.get("/api/v1/community-stats")).json()
    assert stats["total_draws"] == 6
    assert stats["random_draws"] == 3
    assert len(stats["pair_top"]) <= 10

    personal = await client.get("/api/v1/me/stats")
    assert personal.status_code == 200
    assert personal.json()["total_draws"] == 6
    assert personal.json()["random_draws"] == 3


async def test_personal_report_includes_all_history_and_cooking_pairs(client: httpx.AsyncClient, monkeypatch) -> None:
    catalog = (await client.get("/api/v1/catalog")).json()
    akito_name = catalog["akito"][0]["name"]
    toya_name = catalog["toya"][0]["name"]
    results = (
        DrawResult(akito_name, toya_name, True, None, True),
        DrawResult(akito_name, toya_name, False, None, False),
        DrawResult(None, None, False, "foxbun", True),
    )
    monkeypatch.setattr("app.services.draw_service.draw_results", lambda *_args, **_kwargs: results)
    for _batch in range(21):
        response = await client.post(
            "/api/v1/draw",
            json={"count": 3, "fixed_side": "akito", "fixed_name": akito_name},
            headers={"Origin": "http://testserver"},
        )
        assert response.status_code == 200

    page = (await client.get("/api/v1/me?recent_limit=1")).json()
    assert len(page["recent"]) == 1
    assert page["recent"][0]["category_id"] == "foxrabbit"
    response = await client.get("/api/v1/me/report")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    report = response.json()
    assert report["draw_count"] == 63
    assert report["cooking_count"] == 42
    assert report["recent"] == []
    assert report["akito_top"][0]["count"] == 42
    assert report["toya_top"][0]["count"] == 42
    assert report["cooking_pairs"] == [{
        "akito_name": akito_name,
        "toya_name": toya_name,
        "count": 21,
        "akito_avatar_url": catalog["akito"][0]["avatar_url"],
        "toya_avatar_url": catalog["toya"][0]["avatar_url"],
    }]
    foxbun = next(item for item in report["special_counts"] if item["id"] == "foxbun")
    assert foxbun["count"] == 21
    assert foxbun["category_id"] == "foxrabbit"
    assert foxbun["counts_as_cooking"] is True
    assert "asset_urls" in foxbun

    client.cookies.clear()
    other_report = (await client.get("/api/v1/me/report")).json()
    assert other_report["draw_count"] == 0
    assert other_report["cooking_pairs"] == []


async def test_adaptive_draw_cooldown_uses_daily_tiers(settings: Settings) -> None:
    limited_settings = replace(settings, draw_cooldown_seconds=5)
    app = create_app(limited_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as api_client:
            headers = {"Origin": "http://testserver"}
            first = await api_client.post(
                "/api/v1/draw",
                json={"count": 1, "fixed_side": "none", "fixed_name": None},
                headers=headers,
            )
            assert first.status_code == 200
            refreshed_profile = await api_client.get("/api/v1/me")
            assert refreshed_profile.status_code == 200
            assert 1 <= refreshed_profile.json()["cooldown_remaining_seconds"] <= 5
            blocked = await api_client.post(
                "/api/v1/draw",
                json={"count": 1, "fixed_side": "none", "fixed_name": None},
                headers=headers,
            )
            assert blocked.status_code == 429
            assert blocked.json()["error"]["code"] == "draw_limited"
            assert 1 <= int(blocked.headers["retry-after"]) <= 5

            cleared = await api_client.delete("/api/v1/me/history", headers=headers)
            assert cleared.status_code == 200
            still_blocked = await api_client.post(
                "/api/v1/draw",
                json={"count": 1, "fixed_side": "none", "fixed_name": None},
                headers=headers,
            )
            assert still_blocked.status_code == 429

            repository = app.state.services.repository
            async with repository.connect() as connection:
                visitor = await (await connection.execute("SELECT id FROM visitors LIMIT 1")).fetchone()
                await connection.execute(
                    "UPDATE draw_limits SET last_draw_at = ?, daily_results = 200 WHERE visitor_id = ?",
                    (datetime.now(UTC).isoformat().replace("+00:00", "Z"), visitor["id"]),
                )
                await connection.commit()
            tier_blocked = await api_client.post(
                "/api/v1/draw",
                json={"count": 1, "fixed_side": "none", "fixed_name": None},
                headers=headers,
            )
            assert tier_blocked.status_code == 429
            assert 18 <= int(tier_blocked.headers["retry-after"]) <= 20
            tier_profile = await api_client.get("/api/v1/me")
            assert 18 <= tier_profile.json()["cooldown_remaining_seconds"] <= 20

            async with repository.connect() as connection:
                await connection.execute(
                    "UPDATE draw_limits SET quota_day = ?, last_draw_at = ?, daily_results = 499 WHERE visitor_id = ?",
                    ("2000-01-01", datetime.now(UTC).isoformat().replace("+00:00", "Z"), visitor["id"]),
                )
                await connection.commit()
            reset_profile = await api_client.get("/api/v1/me")
            assert reset_profile.json()["cooldown_remaining_seconds"] == 0
            reset_draw = await api_client.post(
                "/api/v1/draw",
                json={"count": 1, "fixed_side": "none", "fixed_name": None},
                headers=headers,
            )
            assert reset_draw.status_code == 200


@pytest.mark.xfail(
    sys.platform == "win32",
    reason=(
        "Windows 文件系统上并发打开同一个新建库会概率性返回 SQLITE_READONLY"
        "（attempt to write a readonly database），与本项目逻辑无关。"
        "生产 python:3.12-slim / UID 10001 / /data bind mount 已用 6 线程 × 5 轮探针实测全通过，"
        "docker logs 中该错误出现 0 次，故 Windows 上按已知环境问题处理。"
    ),
    strict=False,
)
async def test_concurrent_draws_are_all_persisted(client: httpx.AsyncClient) -> None:
    await client.post("/api/v1/session", json={}, headers={"Origin": "http://testserver"})
    responses = await asyncio.gather(
        *[
            client.post(
                "/api/v1/draw",
                json={"count": 3, "fixed_side": "none", "fixed_name": None},
                headers={"Origin": "http://testserver"},
            )
            for _ in range(5)
        ]
    )
    assert all(response.status_code == 200 for response in responses)
    profile = await client.get("/api/v1/me")
    assert profile.json()["draw_count"] == 15
    first_page = await client.get("/api/v1/me?recent_limit=5")
    second_page = await client.get("/api/v1/me?recent_offset=5&recent_limit=5")
    assert first_page.json()["recent_total"] == 15
    assert first_page.json()["recent_has_more"] is True
    assert len(first_page.json()["recent"]) == 5
    assert len(second_page.json()["recent"]) == 5
    assert {item["id"] for item in first_page.json()["recent"]}.isdisjoint(
        item["id"] for item in second_page.json()["recent"]
    )


def _path_of(asset_url: str) -> str:
    """去掉图片 URL 尾部的 `?v=<mtime>`，方便断言落在哪张图上。"""
    return unquote(asset_url.split("?")[0])


class _ForcedFoxbun:
    """固定摇到狐兔饭并取第二张变体，用来端到端验证轮换与去重。"""

    def random(self) -> float:
        return 0.0

    def choice(self, values):
        return values[1 % len(values)]

    def choices(self, population, weights, *, k):
        return [population[3 % len(population)]]


async def test_foxbun_rotation_returns_one_url_and_dedupes(client: httpx.AsyncClient, monkeypatch) -> None:
    monkeypatch.setattr("app.domain.draw.random.SystemRandom", _ForcedFoxbun)

    draw = await client.post(
        "/api/v1/draw",
        json={"count": 3, "fixed_side": "none", "fixed_name": None},
        headers={"Origin": "http://testserver"},
    )
    assert draw.status_code == 200
    results = draw.json()["results"]
    # 同一次抽取里狐兔饭最多一次，重复命中改派给同类其余分支
    assert [result["special_type"] for result in results] == ["foxbun", "pancake", "pancake"]

    foxbun_urls = results[0]["special_asset_urls"]
    assert len(foxbun_urls) == 1
    assert _path_of(foxbun_urls[0]).endswith("fox&rabbit/狐&兔_2.png")

    # 历史回看用抽卡时存下来的那张，反复读不变
    for _ in range(2):
        recent = (await client.get("/api/v1/me")).json()["recent"]
        foxbun_record = next(item for item in recent if item["special_type"] == "foxbun")
        assert foxbun_record["special_asset_urls"] == foxbun_urls


async def test_legacy_record_without_stored_asset_falls_back_to_main(client: httpx.AsyncClient, monkeypatch) -> None:
    # 老记录：special_asset 为 NULL
    monkeypatch.setattr(
        "app.services.draw_service.draw_results",
        lambda *_args, **_kwargs: [DrawResult(None, None, False, "foxbun", True)],
    )
    draw = await client.post(
        "/api/v1/draw",
        json={"count": 1, "fixed_side": "none", "fixed_name": None},
        headers={"Origin": "http://testserver"},
    )
    assert draw.status_code == 200
    main_urls = draw.json()["results"][0]["special_asset_urls"]
    assert len(main_urls) == 1
    assert _path_of(main_urls[0]).endswith("fox&rabbit/狐&兔.png")

    profile = (await client.get("/api/v1/me")).json()
    assert profile["recent"][0]["special_asset_urls"] == main_urls


async def test_legacy_database_gains_special_asset_column(settings: Settings) -> None:
    legacy_schema = "\n".join(
        line for line in SCHEMA.splitlines() if "special_asset" not in line
    )
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(settings.database_path) as connection:
        await connection.executescript(legacy_schema)
        await connection.commit()

    repository = SQLiteRepository(settings.database_path)
    await repository.initialize()
    await repository.initialize()  # 幂等：再跑一次不报错

    async with repository.connect() as connection:
        columns = {
            row["name"]
            for row in await (
                await connection.execute("PRAGMA table_info(draw_results)")
            ).fetchall()
        }
    assert "special_asset" in columns


async def test_mutations_require_allowed_origin(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/session", json={})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invalid_origin"


async def test_unknown_api_uses_error_contract(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/missing")
    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": "接口不存在", "details": {}}
    }
