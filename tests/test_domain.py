from __future__ import annotations

from dataclasses import replace

import pytest

from app.domain.draw import draw_results, fuzzy_match
from app.domain.models import CatalogSnapshot, DomainError, EggCategory, SpecialOutcome
from app.repositories.sqlite import cooldown_for_daily_results


class FakeRandom:
    def __init__(self, random_values: list[float], choices: list[int] | None = None) -> None:
        self.random_values = iter(random_values)
        self.choice_indexes = iter(choices or [0] * 20)
        self.random_calls = 0
        self.seen_weights: list[list[float]] = []

    def random(self) -> float:
        self.random_calls += 1
        return next(self.random_values)

    def choice(self, values):
        return values[0]

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
        )
        for outcome_id, label, tags, assets in (
            ("fox", "狐狸", ("fox",), ("狐",)),
            ("rabbit", "兔子", ("rabbit",), ("兔",)),
            ("foxrabbit", "狐兔", ("fox", "rabbit"), ("狐", "兔")),
            ("foxbun", "狐兔饭", ("fox", "rabbit"), ("狐&兔",)),
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
        special_assets={},
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
    assert rng.seen_weights == [[1, 1, 1, 1]] * 4


def test_special_outcomes_can_repeat_in_batch(snapshot: CatalogSnapshot) -> None:
    special, cooking = snapshot.egg_categories
    snapshot = replace(
        snapshot,
        egg_categories=(replace(special, rate=1.0), replace(cooking, rate=0.0)),
    )
    results = draw_results(snapshot, 2, rng=FakeRandom([0, 0], choices=[0, 0]))
    assert [result.special_type for result in results] == ["fox", "fox"]


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
    [(0, 20), (99, 20), (100, 60), (200, 600), (300, 1800), (400, 3600), (500, 7200)],
)
def test_adaptive_cooldown_tiers(daily_results: int, expected_seconds: int) -> None:
    assert cooldown_for_daily_results(daily_results) == expected_seconds
