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
    categories = sorted(
        snapshot.egg_categories,
        key=lambda category: category.priority,
        reverse=True,
    )
    results: list[DrawResult] = []
    used_unique_ids: set[str] = set()

    for _ in range(count):
        akito_name = fixed_akito or random_source.choice(snapshot.akito_pool)
        toya_name = fixed_toya or random_source.choice(snapshot.toya_pool)
        roll = random_source.random()
        cumulative_rate = 0.0
        category = None
        for candidate in categories:
            cumulative_rate += candidate.rate
            if roll < cumulative_rate:
                category = candidate
                break
        is_cooking = bool(category and category.result_type == "cooking")
        special = None
        chosen_asset = None
        if category and category.result_type == "special":
            # 先剔掉本次已命中的唯一分支，再摇一次定音 —— 不是「摇中再补摇」，
            # 后者会多消耗一次随机数，也会稀释其余分支的概率。
            pool = [
                outcome
                for outcome in category.outcomes
                if not (outcome.unique_per_draw and outcome.id in used_unique_ids)
            ] or list(category.outcomes)
            special = random_source.choices(
                pool,
                [outcome.weight for outcome in pool],
                k=1,
            )[0]
            if special.unique_per_draw:
                used_unique_ids.add(special.id)
            # 每个 assets 槽位各挑一张（Bot 的 fox_icon_uris 也是这么做的）：
            # 「狐+兔」这种两张图的结果会各轮各的，不会被挤成一张。
            variants = snapshot.special_variant_urls.get(special.id, {})
            chosen_asset = tuple(
                random_source.choice(stems) if (stems := tuple(variants.get(asset, ()))) else None
                for asset in special.assets
            )
        if special:
            results.append(
                DrawResult(
                    akito_name=None,
                    toya_name=None,
                    is_cooking=False,
                    special_type=special.id,
                    counts_as_cooking=special.counts_as_cooking,
                    special_asset_stems=chosen_asset,
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

