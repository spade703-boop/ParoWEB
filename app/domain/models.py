from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Side = Literal["akito", "toya"]
FixedSide = Literal["none", "akito", "toya"]
EggResultType = Literal["special", "cooking"]


@dataclass(frozen=True, slots=True)
class SpecialOutcome:
    id: str
    category_id: str
    label: str
    weight: float
    tags: tuple[str, ...]
    assets: tuple[str, ...]
    message: str
    counts_as_cooking: bool
    # 同一次抽取内该结果最多出现一次（狐兔饭用）。默认 False，老配置不受影响。
    unique_per_draw: bool = False


@dataclass(frozen=True, slots=True)
class EggCategory:
    id: str
    rate: float
    priority: int
    result_type: EggResultType
    outcomes: tuple[SpecialOutcome, ...]


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    version: str
    akito_pool: tuple[str, ...]
    toya_pool: tuple[str, ...]
    egg_categories: tuple[EggCategory, ...]
    avatars: dict[str, dict[str, str | None]]
    special_assets: dict[str, tuple[str, ...]]
    # 特殊结果 id -> assets 槽位 -> {候选图 stem: URL}（主图排在前面）。
    # 抽卡时每个槽位各挑一张存进记录；槽位各自轮换，不会把「狐+兔」挤成一张。
    special_variant_urls: dict[str, dict[str, dict[str, str]]]

    @property
    def special_outcomes(self) -> tuple[SpecialOutcome, ...]:
        return tuple(
            outcome
            for category in self.egg_categories
            for outcome in category.outcomes
        )

    def outcome_map(self) -> dict[str, SpecialOutcome]:
        return {outcome.id: outcome for outcome in self.special_outcomes}

    def asset_urls_for(
        self,
        outcome_id: str | None,
        stored_stems: tuple[str | None, ...] | None = None,
    ) -> list[str]:
        """该特殊结果这次要显示的图：优先用记录里存的那些，取不齐就整组回落主图。

        `stored_stems` 为空覆盖两种情况（都显示主图）：改动前的老记录、以及存的那张图后来被删了。
        数量对不上（比如后来改了 assets）也整组回落，避免出现「只画了一半」的卡片。
        """
        if not outcome_id:
            return []
        outcome = self.outcome_map().get(outcome_id)
        assets = outcome.assets if outcome else ()
        if assets and stored_stems and len(stored_stems) == len(assets):
            table = self.special_variant_urls.get(outcome_id, {})
            urls: list[str] = []
            for asset, stem in zip(assets, stored_stems):
                url = table.get(asset, {}).get(stem) if stem else None
                if not url:
                    break
                urls.append(url)
            else:
                return urls
        return list(self.special_assets.get(outcome_id, ()))


@dataclass(frozen=True, slots=True)
class DrawResult:
    akito_name: str | None
    toya_name: str | None
    is_cooking: bool
    special_type: str | None
    counts_as_cooking: bool
    # 本次抽中的候选图 stem，与 outcome.assets 一一对应（多图轮换用）。
    # 老记录是空元组，回看时回落主图。
    special_asset_stems: tuple[str | None, ...] = ()


class DomainError(ValueError):
    def __init__(self, code: str, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class DrawLimitError(Exception):
    def __init__(self, message: str, *, retry_after: int, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after
        self.details = details or {}
