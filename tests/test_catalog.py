from __future__ import annotations

import json

from app.services.catalog import CatalogService


def _write_catalog(root, *, akito_pool, cooking_rate=0.03) -> None:
    (root / "paro_pools.json").write_text(
        json.dumps({"akito_pool": akito_pool, "toya_pool": ["冬弥一"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (root / "paro_config.json").write_text(
        json.dumps(
            {
                "cooking_rate": cooking_rate,
                "special_rate": 0.08,
                "special_outcomes": [
                    {
                        "id": "test",
                        "label": "测试彩蛋",
                        "weight": 1,
                        "tags": ["test"],
                        "assets": [],
                        "message": "测试",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_catalog_reloads_bot_data_changes(tmp_path) -> None:
    _write_catalog(tmp_path, akito_pool=["彰人一"])
    service = CatalogService(tmp_path)
    assert service.snapshot.akito_pool == ("彰人一",)

    _write_catalog(tmp_path, akito_pool=["彰人一", "彰人二"], cooking_rate=0.05)
    snapshot = service.snapshot
    assert snapshot.akito_pool == ("彰人一", "彰人二")
    assert snapshot.cooking_rate == 0.05
