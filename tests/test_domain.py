from __future__ import annotations

from dataclasses import replace

import pytest

from app.domain.draw import draw_results, fuzzy_match
from app.domain.models import CatalogSnapshot, DomainError, SpecialOutcome
from app.repositories.sqlite import cooldown_for_daily_results


class FakeRandom:
    def __init__(self, random_values: list[float], choices: list[int] | None = None) -> None:
        self.random_values = iter(random_values)
        self.choice_indexes = iter(choices or [0] * 20)

    def random(self) -> float:
        return next(self.random_values)

    def choice(self, values):
        return values[next(self.choice_indexes) % len(values)]

    def choices(self, population, weights, *, k):
        return [population[0]]


@pytest.fixture
def snapshot() -> CatalogSnapshot:
    outcomes = (
        SpecialOutcome("fox", "狐狸", 1, ("fox",), ("狐",), "狐狸出现", False),
        SpecialOutcome("rabbit", "兔子", 1, ("rabbit",), ("兔",), "兔子出现", False),
    )
    return CatalogSnapshot(
        version="test",
        akito_pool=("黑百合", "白骑"),
        toya_pool=("王子冬", "黑骑"),
        cooking_rate=.03,
        special_rate=.08,
        special_outcomes=outcomes,
        avatars={"akito": {}, "toya": {}},
        special_assets={},
    )


def test_fuzzy_match_prefers_exact_and_rejects_ambiguity() -> None:
    assert fuzzy_match("白骑", ["白骑", "白骑士"]) == "白骑"
    with pytest.raises(DomainError) as error:
        fuzzy_match("白", ["白骑", "白百合"])
    assert error.value.code == "ambiguous_fixed_name"


def test_fixed_draw_and_cooking_precedes_special(snapshot: CatalogSnapshot) -> None:
    results = draw_results(
        snapshot,
        1,
        fixed_side="akito",
        fixed_name="黑百",
        rng=FakeRandom([0.01]),
    )
    assert results[0].akito_name == "黑百合"
    assert results[0].is_cooking is True
    assert results[0].special_type is None


def test_special_tags_do_not_repeat_in_batch(snapshot: CatalogSnapshot) -> None:
    snapshot = replace(snapshot, special_rate=1.0, cooking_rate=0.0)
    results = draw_results(snapshot, 2, rng=FakeRandom([.5, 0, .5, 0]))
    assert [result.special_type for result in results] == ["fox", "rabbit"]


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
