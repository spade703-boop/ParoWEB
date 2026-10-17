from __future__ import annotations

from dataclasses import replace

import pytest

from app.domain.draw import draw_results, fuzzy_match
from app.domain.models import CatalogSnapshot, DomainError, EggCategory, SpecialOutcome
from app.repositories.sqlite import cooldown_for_daily_results


class FakeRandom:
    def __init__(
        self,
        random_values: list[float],
        choices: list[int] | None = None,
        picked: list[int] | None = None,
    ) -> None:
        self.random_values = iter(random_values)
        self.choice_indexes = iter(choices or [0] * 20)
        # choice() 取第几个元素（多图轮换选变体用）；默认取第一个。
        self.picked_indexes = iter(picked or [0] * 20)
        self.random_calls = 0
        self.seen_weights: list[list[float]] = []

    def random(self) -> float:
        self.random_calls += 1
        return next(self.random_values)

    def choice(self, values):
        return values[next(self.picked_indexes) % len(values)]

    def choices(self, population, weights, *, k):
        self.seen_weights.append(list(weights))
        return [population[next(self.choice_indexes) % len(population)]]


@pytest.fixture
def snapshot() -> CatalogSnapshot:
    outcomes = tuple(
        SpecialOutcome(
            id=outcome_id,
            category_id="foxrabbit",
            label=label,
            weight=1,
            tags=tags,
            assets=assets,
            message=f"{label}出现",
            counts_as_cooking=outcome_id == "foxbun",
            unique_per_draw=outcome_id == "foxbun",
        )
        for outcome_id, label, tags, assets in (
            ("fox", "狐狸", ("fox",), ("狐",)),
            ("rabbit", "兔子", ("rabbit",), ("兔",)),
            ("foxrabbit", "狐兔", ("fox", "rabbit"), ("狐", "兔")),
            ("foxbun", "狐兔饭", ("fox", "rabbit"), ("狐&兔",)),
            ("pancake", "松饼", ("fox",), ("松饼",)),
            ("cookie", "曲奇", ("rabbit",), ("曲奇",)),
        )
    )
    return CatalogSnapshot(
        version="test",
        akito_pool=("黑百合", "白骑"),
        toya_pool=("王子冬", "黑骑"),
        egg_categories=(
            EggCategory("foxrabbit", .03, 100, "special", outcomes),
            EggCategory("cooking", .03, 0, "cooking", ()),
        ),
        avatars={"akito": {}, "toya": {}},
        special_assets={
            "foxbun": ("/content/狐&兔.png",),
            "foxrabbit": ("/content/狐.png", "/content/兔.png"),
        },
        special_variant_urls={
            "foxbun": {"狐&兔": {"狐&兔": "/content/狐&兔.png", "狐&兔_2": "/content/狐&兔_2.png"}},
            "foxrabbit": {"狐": {"狐": "/content/狐.png"}, "兔": {"兔": "/content/兔.png"}},
        },
    )


def test_fuzzy_match_prefers_exact_and_rejects_ambiguity() -> None:
    assert fuzzy_match("白骑", ["白骑", "白骑士"]) == "白骑"
    with pytest.raises(DomainError) as error:
        fuzzy_match("白", ["白骑", "白百合"])
    assert error.value.code == "ambiguous_fixed_name"


def test_fixed_draw_keeps_resolved_name_for_cooking(snapshot: CatalogSnapshot) -> None:
    results = draw_results(
        snapshot,
        1,
        fixed_side="akito",
        fixed_name="黑百",
        rng=FakeRandom([0.03]),
    )
    assert results[0].akito_name == "黑百合"
    assert results[0].is_cooking is True
    assert results[0].special_type is None


@pytest.mark.parametrize(
    ("roll", "expected_cooking", "expected_special"),
    [
        (0, False, "fox"),
        (0.029999, False, "fox"),
        (0.03, True, None),
        (0.059999, True, None),
        (0.06, False, None),
    ],
)
def test_category_probability_boundaries(
    snapshot: CatalogSnapshot,
    roll: float,
    expected_cooking: bool,
    expected_special: str | None,
) -> None:
    result = draw_results(snapshot, 1, rng=FakeRandom([roll]))[0]
    assert result.is_cooking is expected_cooking
    assert result.special_type == expected_special
    assert not (result.is_cooking and result.special_type)


def test_category_selection_uses_one_random_value_per_result(snapshot: CatalogSnapshot) -> None:
    rng = FakeRandom([0.06, 0.06, 0.06])
    draw_results(snapshot, 3, rng=rng)
    assert rng.random_calls == 3


def test_special_outcomes_use_configured_weights(snapshot: CatalogSnapshot) -> None:
    rng = FakeRandom([0, 0, 0, 0], choices=[0, 1, 2, 3])
    results = [draw_results(snapshot, 1, rng=rng)[0] for _ in range(4)]
    assert [result.special_type for result in results] == [
        "fox",
        "rabbit",
        "foxrabbit",
        "foxbun",
    ]
    assert rng.seen_weights == [[1, 1, 1, 1, 1, 1]] * 4


def test_special_outcomes_can_repeat_in_batch(snapshot: CatalogSnapshot) -> None:
    special, cooking = snapshot.egg_categories
    snapshot = replace(
        snapshot,
        egg_categories=(replace(special, rate=1.0), replace(cooking, rate=0.0)),
    )
    results = draw_results(snapshot, 2, rng=FakeRandom([0, 0], choices=[0, 0]))
    assert [result.special_type for result in results] == ["fox", "fox"]


def _force_special(snapshot: CatalogSnapshot, rate: float = 1.0) -> CatalogSnapshot:
    """把狐兔类拉到必然命中，方便断言类内行为。"""
    special, cooking = snapshot.egg_categories
    return replace(
        snapshot,
        egg_categories=(replace(special, rate=rate), replace(cooking, rate=0.0)),
    )


def _drop_cooking(snapshot: CatalogSnapshot) -> tuple[EggCategory, ...]:
    cooking = next(
        category for category in snapshot.egg_categories if category.result_type == "cooking"
    )
    return (replace(cooking, rate=0.0),)


def test_unique_outcome_appears_at_most_once_per_draw(snapshot: CatalogSnapshot) -> None:
    results = draw_results(_force_special(snapshot), 3, rng=FakeRandom([0, 0, 0], choices=[3, 3, 3]))
    types = [result.special_type for result in results]
    assert types.count("foxbun") == 1
    # 狐兔饭被剔掉后，摇到的下标落到同类其余分支上（松饼可重复 —— 它没标唯一）
    assert types == ["foxbun", "pancake", "pancake"]


def test_dedupe_only_drops_that_branch_and_keeps_one_roll(snapshot: CatalogSnapshot) -> None:
    rng = FakeRandom([0, 0], choices=[3, 3])
    draw_results(_force_special(snapshot), 2, rng=rng)
    # 第一次六个分支等权，第二次剔掉已命中的狐兔饭 —— 每次仍只摇一次，概率不稀释
    assert rng.seen_weights == [[1, 1, 1, 1, 1, 1], [1, 1, 1, 1, 1]]
    assert rng.random_calls == 2


def test_unique_only_candidate_does_not_loop_forever(snapshot: CatalogSnapshot) -> None:
    special, _cooking = snapshot.egg_categories
    foxbun = next(outcome for outcome in special.outcomes if outcome.id == "foxbun")
    only = replace(
        snapshot,
        egg_categories=(replace(special, rate=1.0, outcomes=(foxbun,)), *_drop_cooking(snapshot)),
    )
    results = draw_results(only, 3, rng=FakeRandom([0, 0, 0]))
    assert [result.special_type for result in results] == ["foxbun"] * 3


def test_branch_without_unique_flag_can_repeat(snapshot: CatalogSnapshot) -> None:
    special, _cooking = snapshot.egg_categories
    outcomes = tuple(replace(outcome, unique_per_draw=False) for outcome in special.outcomes)
    snapshot = replace(
        snapshot,
        egg_categories=(replace(special, rate=1.0, outcomes=outcomes), *_drop_cooking(snapshot)),
    )
    results = draw_results(snapshot, 2, rng=FakeRandom([0, 0], choices=[3, 3]))
    assert [result.special_type for result in results] == ["foxbun", "foxbun"]


def test_draw_picks_a_specific_variant(snapshot: CatalogSnapshot) -> None:
    rng = FakeRandom([0], choices=[3], picked=[0, 0, 1])
    result = draw_results(_force_special(snapshot), 1, rng=rng)[0]
    assert result.special_type == "foxbun"
    assert result.special_asset_stems == ("狐&兔_2",)


def test_multi_asset_outcome_rotates_each_slot(snapshot: CatalogSnapshot) -> None:
    """「狐+兔」有两个 assets 槽位，各轮各的 —— 不能被挤成一张图。"""
    result = draw_results(_force_special(snapshot), 1, rng=FakeRandom([0], choices=[2]))[0]
    assert result.special_type == "foxrabbit"
    assert result.special_asset_stems == ("狐", "兔")
    assert snapshot.asset_urls_for("foxrabbit", result.special_asset_stems) == [
        "/content/狐.png",
        "/content/兔.png",
    ]


def test_no_variant_leaves_special_asset_empty(snapshot: CatalogSnapshot) -> None:
    result = draw_results(_force_special(snapshot), 1, rng=FakeRandom([0], choices=[0]))[0]
    assert result.special_type == "fox"
    assert not any(result.special_asset_stems)


def test_rotation_does_not_consume_extra_random(snapshot: CatalogSnapshot) -> None:
    rng = FakeRandom([0], choices=[3], picked=[0, 0, 1])
    draw_results(_force_special(snapshot), 1, rng=rng)
    assert rng.random_calls == 1


def test_asset_urls_for_prefers_stored_asset_and_falls_back(snapshot: CatalogSnapshot) -> None:
    assert snapshot.asset_urls_for("foxbun", ("狐&兔_2",)) == ["/content/狐&兔_2.png"]
    # 存的那张被删了 / 老记录没存 → 回落主图，不至于开天窗
    assert snapshot.asset_urls_for("foxbun", ("不存在",)) == ["/content/狐&兔.png"]
    assert snapshot.asset_urls_for("foxbun") == ["/content/狐&兔.png"]
    # 槽位数量对不上（后来改过 assets）→ 整组回落，不画半张卡
    assert snapshot.asset_urls_for("foxrabbit", ("狐",)) == ["/content/狐.png", "/content/兔.png"]
    assert snapshot.asset_urls_for("foxrabbit", ()) == ["/content/狐.png", "/content/兔.png"]
    assert snapshot.asset_urls_for(None) == []


def test_new_category_uses_priority_without_changing_draw_flow(snapshot: CatalogSnapshot) -> None:
    meteor = SpecialOutcome(
        id="meteor",
        category_id="stargazing",
        label="流星",
        weight=1,
        tags=(),
        assets=(),
        message="流星出现了",
        counts_as_cooking=False,
    )
    snapshot = replace(
        snapshot,
        egg_categories=(
            *snapshot.egg_categories,
            EggCategory("stargazing", .01, 200, "special", (meteor,)),
        ),
    )
    result = draw_results(snapshot, 1, rng=FakeRandom([0]))[0]
    assert result.special_type == "meteor"


def test_equal_priority_categories_keep_configuration_order(snapshot: CatalogSnapshot) -> None:
    foxrabbit, cooking = snapshot.egg_categories
    first = replace(foxrabbit, rate=.1, priority=10)
    second_outcome = replace(
        foxrabbit.outcomes[0],
        id="second",
        category_id="second",
    )
    second = EggCategory("second", .1, 10, "special", (second_outcome,))
    snapshot = replace(snapshot, egg_categories=(first, second, cooking))
    result = draw_results(snapshot, 1, rng=FakeRandom([.1]))[0]
    assert result.special_type == "second"


@pytest.mark.parametrize("count", [0, 4, -1])
def test_invalid_count(snapshot: CatalogSnapshot, count: int) -> None:
    with pytest.raises(DomainError):
        draw_results(snapshot, count)


@pytest.mark.parametrize(
    ("daily_results", "expected_seconds"),
    [
        (0, 5),
        (99, 5),
        (100, 10),
        (200, 20),
        (300, 30),
        (400, 45),
        (500, 60),
        (700, 120),
        (900, 300),
        (1200, 300),
    ],
)
def test_adaptive_cooldown_tiers(daily_results: int, expected_seconds: int) -> None:
    assert cooldown_for_daily_results(daily_results) == expected_seconds
