from __future__ import annotations

import json

import pytest

from app.domain.draw import draw_results
from app.services.catalog import CatalogError, CatalogService


class _FixedRandom:
    """最小随机源：random() 返回固定值，choices() 返回指定下标。"""

    def __init__(self, roll: float, index: int = 0) -> None:
        self._roll = roll
        self._index = index

    def random(self) -> float:
        return self._roll

    def choice(self, values):
        return values[0]

    def choices(self, population, weights, *, k):
        return [population[self._index % len(population)]]


def _outcome(outcome_id: str = "test", **overrides) -> dict:
    value = {
        "id": outcome_id,
        "label": outcome_id,
        "weight": 1,
        "assets": [],
        "message": f"{outcome_id}出现",
    }
    value.update(overrides)
    return value


def _special_category(
    category_id: str = "special",
    outcome_id: str = "test",
    **overrides,
) -> dict:
    value = {
        "id": category_id,
        "rate": 0.08,
        "priority": 100,
        "result_type": "special",
        "outcomes": [_outcome(outcome_id)],
    }
    value.update(overrides)
    return value


def _write_catalog(root, *, akito_pool=None, config=None) -> None:
    (root / "paro_pools.json").write_text(
        json.dumps(
            {"akito_pool": akito_pool or ["彰人一"], "toya_pool": ["冬弥一"]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "paro_config.json").write_text(
        json.dumps(
            config
            or {
                "cooking_rate": 0.03,
                "special_rate": 0.08,
                "special_outcomes": [_outcome(tags=["test"])],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_legacy_catalog_is_normalized_and_reloads(tmp_path) -> None:
    _write_catalog(tmp_path)
    service = CatalogService(tmp_path)
    snapshot = service.snapshot
    assert [(category.id, category.rate) for category in snapshot.egg_categories] == [
        ("foxrabbit", 0.08),
        ("cooking", 0.03),
    ]
    assert snapshot.special_outcomes[0].category_id == "foxrabbit"

    _write_catalog(
        tmp_path,
        akito_pool=["彰人一", "彰人二"],
        config={
            "cooking_rate": 0.05,
            "special_rate": 0.08,
            "special_outcomes": [_outcome(tags=[])],
        },
    )
    reloaded = service.snapshot
    assert reloaded.akito_pool == ("彰人一", "彰人二")
    assert reloaded.egg_categories[1].rate == 0.05
    assert reloaded.version != snapshot.version


def test_new_catalog_flattens_outcomes_across_categories(tmp_path) -> None:
    _write_catalog(
        tmp_path,
        config={
            "egg_categories": [
                _special_category("foxrabbit", "fox", rate=0.03),
                _special_category("stargazing", "meteor", rate=0.01, priority=200),
                {
                    "id": "cooking",
                    "rate": 0.03,
                    "priority": 0,
                    "result_type": "cooking",
                },
            ]
        },
    )
    snapshot = CatalogService(tmp_path).snapshot
    assert [category.id for category in snapshot.egg_categories] == [
        "foxrabbit",
        "stargazing",
        "cooking",
    ]
    assert [outcome.id for outcome in snapshot.special_outcomes] == ["fox", "meteor"]
    assert snapshot.outcome_map()["meteor"].category_id == "stargazing"
    assert snapshot.outcome_map()["meteor"].tags == ()


def test_extra_outcome_in_existing_category_needs_no_draw_change(tmp_path) -> None:
    outcomes = [
        _outcome("fox"),
        _outcome("rabbit", weight=2),
        _outcome("foxrabbit"),
        _outcome("foxbun", counts_as_cooking=True),
        _outcome("dogfox"),
    ]
    _write_catalog(
        tmp_path,
        config={
            "egg_categories": [
                _special_category("foxrabbit", rate=0.03, outcomes=outcomes),
                {
                    "id": "cooking",
                    "rate": 0.03,
                    "priority": 0,
                    "result_type": "cooking",
                },
            ]
        },
    )
    snapshot = CatalogService(tmp_path).snapshot

    assert [outcome.id for outcome in snapshot.special_outcomes] == [
        "fox",
        "rabbit",
        "foxrabbit",
        "foxbun",
        "dogfox",
    ]
    # 抽取主流程只按大类 outcomes 加权，第五个结果无需额外代码
    picked = draw_results(snapshot, 1, rng=_FixedRandom(0.0, index=4))
    assert picked[0].special_type == "dogfox"


def test_priority_defaults_to_zero_and_accepts_integral_float(tmp_path) -> None:
    without_priority = _special_category("no-priority", "first")
    without_priority.pop("priority")
    _write_catalog(
        tmp_path,
        config={
            "egg_categories": [
                without_priority,
                _special_category("float-priority", "second", priority=100.0),
            ]
        },
    )
    snapshot = CatalogService(tmp_path).snapshot
    priorities = {category.id: category.priority for category in snapshot.egg_categories}
    assert priorities == {"no-priority": 0, "float-priority": 100}
    assert all(isinstance(value, int) for value in priorities.values())


@pytest.mark.parametrize(
    "config",
    [
        {"egg_categories": []},
        {
            "egg_categories": [
                _special_category("duplicate", "first"),
                _special_category("duplicate", "second"),
            ]
        },
        {
            "egg_categories": [
                _special_category("first", "duplicate"),
                _special_category("second", "duplicate"),
            ]
        },
        {
            "egg_categories": [
                _special_category(result_type="unknown"),
            ]
        },
        {
            "egg_categories": [
                _special_category(outcomes=[_outcome(weight=0)]),
            ]
        },
        {
            "egg_categories": [
                _special_category("first", "first", rate=0.6),
                _special_category("second", "second", rate=0.5),
            ]
        },
        {
            "egg_categories": [
                _special_category(priority="high"),
            ]
        },
    ],
)
def test_invalid_new_catalog_is_rejected(tmp_path, config: dict) -> None:
    _write_catalog(tmp_path, config=config)
    with pytest.raises(CatalogError):
        CatalogService(tmp_path).load()


def _fox_assets(tmp_path, filenames: dict[str, int]) -> None:
    _special_assets(tmp_path, "fox&rabbit", filenames)


def _special_assets(tmp_path, folder_name: str, filenames: dict[str, int]) -> None:
    folder = tmp_path / "images" / "paro_avatars" / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    for name, size in filenames.items():
        (folder / name).write_bytes(b"\0" * size)


def _foxbun_config() -> dict:
    return {
        "egg_categories": [
            _special_category(
                "foxrabbit",
                rate=0.03,
                outcomes=[_outcome("foxbun", assets=["狐&兔"])],
            ),
            {
                "id": "cooking",
                "rate": 0.03,
                "priority": 0,
                "result_type": "cooking",
            },
        ]
    }


def test_variant_stems_follow_main_then_numbered_order(tmp_path) -> None:
    _write_catalog(tmp_path, config=_foxbun_config())
    _fox_assets(
        tmp_path,
        {
            "狐&兔.png": 16,
            "狐&兔_10.png": 16,
            "狐&兔_2.png": 16,
            "狐&兔_2_副本.png": 16,
            "狐&兔2.png": 16,
            "狐&兔草稿.png": 16,
        },
    )
    snapshot = CatalogService(tmp_path).snapshot
    assert tuple(snapshot.special_variant_urls["foxbun"]["狐&兔"]) == (
        "狐&兔",
        "狐&兔_2",
        "狐&兔_10",
    )


def test_variant_scan_does_not_touch_neighbours(tmp_path) -> None:
    _write_catalog(tmp_path, config=_foxbun_config())
    _fox_assets(tmp_path, {"狐&兔.png": 16, "狐.png": 16, "兔.png": 16, "松饼.png": 16, "曲奇.png": 16})
    snapshot = CatalogService(tmp_path).snapshot
    assert tuple(snapshot.special_variant_urls["foxbun"]["狐&兔"]) == ("狐&兔",)


def test_empty_variant_file_is_skipped(tmp_path) -> None:
    _write_catalog(tmp_path, config=_foxbun_config())
    _fox_assets(tmp_path, {"狐&兔.png": 16, "狐&兔_2.png": 0})
    snapshot = CatalogService(tmp_path).snapshot
    assert tuple(snapshot.special_variant_urls["foxbun"]["狐&兔"]) == ("狐&兔",)


def test_unique_per_draw_is_parsed_and_defaults_to_false(tmp_path) -> None:
    _write_catalog(
        tmp_path,
        config={
            "egg_categories": [
                _special_category(
                    "foxrabbit",
                    rate=0.03,
                    outcomes=[
                        _outcome("marked", unique_per_draw=True),
                        _outcome("plain"),
                    ],
                )
            ]
        },
    )
    snapshot = CatalogService(tmp_path).snapshot
    flags = {outcome.id: outcome.unique_per_draw for outcome in snapshot.special_outcomes}
    assert flags == {"marked": True, "plain": False}


def test_special_category_uses_configured_directory_layout_and_accents(tmp_path) -> None:
    _write_catalog(
        tmp_path,
        config={
            "egg_categories": [
                _special_category(
                    "dogfox",
                    rate=0.03,
                    asset_dir="dog&fox",
                    accents={"dog": "a", "fox": "t"},
                    outcomes=[
                        _outcome("dog", tags=["dog"], assets=["狗"], layout="single"),
                        _outcome(
                            "doggori",
                            tags=["dog", "fox"],
                            assets=["狗", "狐"],
                            layout="pair",
                        ),
                        _outcome(
                            "doggoribun",
                            tags=["dog", "fox"],
                            assets=["狗&狐"],
                            layout="wide",
                            counts_as_cooking=True,
                        ),
                    ],
                )
            ]
        },
    )
    _special_assets(
        tmp_path,
        "dog&fox",
        {"狗.png": 16, "狐.png": 16, "狗&狐.png": 16, "狗&狐_2.png": 16},
    )

    snapshot = CatalogService(tmp_path).snapshot
    doggori = snapshot.outcome_map()["doggori"]
    doggoribun = snapshot.outcome_map()["doggoribun"]

    assert doggori.asset_dir == "dog&fox"
    assert doggori.layout == "pair"
    assert doggori.accents == {"dog": "a", "fox": "t"}
    assert all("dog&fox" in url for url in snapshot.special_assets["doggori"])
    assert tuple(snapshot.special_variant_urls["doggoribun"]["狗&狐"]) == (
        "狗&狐",
        "狗&狐_2",
    )
    assert doggoribun.counts_as_cooking is True


def test_failed_hot_reload_keeps_previous_snapshot(tmp_path) -> None:
    _write_catalog(tmp_path)
    service = CatalogService(tmp_path)
    previous = service.snapshot

    _write_catalog(tmp_path, config={"egg_categories": []})
    current = service.snapshot

    assert current is previous
    assert current.version == previous.version
