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

    @property
    def special_outcomes(self) -> tuple[SpecialOutcome, ...]:
        return tuple(
            outcome
            for category in self.egg_categories
            for outcome in category.outcomes
        )

    def outcome_map(self) -> dict[str, SpecialOutcome]:
        return {outcome.id: outcome for outcome in self.special_outcomes}


@dataclass(frozen=True, slots=True)
class DrawResult:
    akito_name: str | None
    toya_name: str | None
    is_cooking: bool
    special_type: str | None
    counts_as_cooking: bool


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
