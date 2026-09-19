from __future__ import annotations

import random
from typing import Protocol, Sequence, TypeVar

from .models import CatalogSnapshot, DomainError, DrawResult, FixedSide


T = TypeVar("T")


class RandomSource(Protocol):
    def choice(self, values: Sequence[T]) -> T: ...
    def choices(self, population: Sequence[T], weights: Sequence[float], *, k: int) -> list[T]: ...
    def random(self) -> float: ...


def fuzzy_match(name: str, pool: Sequence[str]) -> str:
    query = name.strip().casefold()
    exact = [entry for entry in pool if entry.casefold() == query]
    if exact:
        return exact[0]
    prefix = [entry for entry in pool if entry.casefold().startswith(query)]
    if len(prefix) == 1:
        return prefix[0]
    candidates = prefix or [entry for entry in pool if query in entry.casefold()]
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        raise DomainError("ambiguous_fixed_name", "指定名称匹配到多个派生，请选择完整名称", {"candidates": candidates})
    raise DomainError("invalid_fixed_name", "指定的派生不存在")


def resolve_fixed_name(snapshot: CatalogSnapshot, fixed_side: FixedSide, fixed_name: str | None) -> tuple[str | None, str | None]:
    if fixed_side == "none":
        if fixed_name and fixed_name.strip():
            raise DomainError("unexpected_fixed_name", "双方随机时不能指定派生名称")
        return None, None
    if not fixed_name or not fixed_name.strip():
        raise DomainError("missing_fixed_name", "请选择要固定的派生")
    if fixed_side == "akito":
        return fuzzy_match(fixed_name, snapshot.akito_pool), None
    return None, fuzzy_match(fixed_name, snapshot.toya_pool)


def draw_results(
    snapshot: CatalogSnapshot,
    count: int,
    *,
    fixed_side: FixedSide = "none",
    fixed_name: str | None = None,
    rng: RandomSource | None = None,
) -> list[DrawResult]:
    if count not in (1, 2, 3):
        raise DomainError("invalid_count", "每次只能抽取 1、2 或 3 个结果")
    fixed_akito, fixed_toya = resolve_fixed_name(snapshot, fixed_side, fixed_name)
    random_source = rng or random.SystemRandom()
    used_tags: set[str] = set()
    results: list[DrawResult] = []

    for _ in range(count):
        akito_name = fixed_akito or random_source.choice(snapshot.akito_pool)
        toya_name = fixed_toya or random_source.choice(snapshot.toya_pool)
        is_cooking = random_source.random() < snapshot.cooking_rate
        special = None
        if not is_cooking and random_source.random() < snapshot.special_rate:
            available = [
                outcome
                for outcome in snapshot.special_outcomes
                if used_tags.isdisjoint(outcome.tags)
            ]
            if available:
                special = random_source.choices(
                    available,
                    [outcome.weight for outcome in available],
                    k=1,
                )[0]
                used_tags.update(special.tags)
        if special:
            results.append(
                DrawResult(
                    akito_name=None,
                    toya_name=None,
                    is_cooking=False,
                    special_type=special.id,
                    counts_as_cooking=special.counts_as_cooking,
                )
            )
        else:
            results.append(
                DrawResult(
                    akito_name=akito_name,
                    toya_name=toya_name,
                    is_cooking=is_cooking,
                    special_type=None,
                    counts_as_cooking=is_cooking,
                )
            )
    return results

