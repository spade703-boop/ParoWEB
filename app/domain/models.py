from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Side = Literal["akito", "toya"]
FixedSide = Literal["none", "akito", "toya"]


@dataclass(frozen=True, slots=True)
class SpecialOutcome:
    id: str
    label: str
    weight: float
    tags: tuple[str, ...]
    assets: tuple[str, ...]
    message: str
    counts_as_cooking: bool


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    version: str
    akito_pool: tuple[str, ...]
    toya_pool: tuple[str, ...]
    cooking_rate: float
    special_rate: float
    special_outcomes: tuple[SpecialOutcome, ...]
    avatars: dict[str, dict[str, str | None]]
    special_assets: dict[str, tuple[str, ...]]

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

