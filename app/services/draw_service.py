from __future__ import annotations

from app.domain.draw import draw_results, resolve_fixed_name
from app.domain.models import FixedSide
from app.repositories.sqlite import SQLiteRepository
from app.services.catalog import CatalogService


class DrawService:
    def __init__(
        self,
        catalog: CatalogService,
        repository: SQLiteRepository,
        *,
        cooldown_seconds: int = 5,
    ) -> None:
        self.catalog = catalog
        self.repository = repository
        self.cooldown_seconds = cooldown_seconds

    async def draw(
        self,
        *,
        visitor_id: str,
        count: int,
        fixed_side: FixedSide,
        fixed_name: str | None,
    ) -> dict:
        snapshot = self.catalog.snapshot
        fixed_akito, fixed_toya = resolve_fixed_name(snapshot, fixed_side, fixed_name)
        resolved_fixed_name = fixed_akito or fixed_toya
        results = draw_results(
            snapshot,
            count,
            fixed_side=fixed_side,
            fixed_name=resolved_fixed_name,
        )
        batch_id, created_at, next_cooldown = await self.repository.create_draw(
            visitor_id=visitor_id,
            requested_count=count,
            fixed_side=fixed_side,
            fixed_name=resolved_fixed_name,
            results=results,
            cooldown_seconds=self.cooldown_seconds,
        )
        outcome_map = snapshot.outcome_map()
        cooking_category_id = next(
            (
                category.id
                for category in snapshot.egg_categories
                if category.result_type == "cooking"
            ),
            None,
        )

        def category_id_for(result) -> str | None:
            if result.special_type:
                return outcome_map[result.special_type].category_id
            if result.is_cooking:
                return cooking_category_id
            return None

        return {
            "batch_id": batch_id,
            "created_at": created_at,
            "next_cooldown_seconds": next_cooldown,
            "results": [
                {
                    "position": position,
                    "akito_name": result.akito_name,
                    "toya_name": result.toya_name,
                    "akito_avatar_url": snapshot.avatars["akito"].get(result.akito_name) if result.akito_name else None,
                    "toya_avatar_url": snapshot.avatars["toya"].get(result.toya_name) if result.toya_name else None,
                    "is_cooking": result.is_cooking,
                    "category_id": category_id_for(result),
                    "special_type": result.special_type,
                    "special_label": outcome_map[result.special_type].label if result.special_type else None,
                    "special_message": outcome_map[result.special_type].message if result.special_type else None,
                    "special_layout": outcome_map[result.special_type].layout if result.special_type else None,
                    "special_tags": list(outcome_map[result.special_type].tags) if result.special_type else [],
                    "special_accents": outcome_map[result.special_type].accents if result.special_type else {},
                    "special_asset_urls": snapshot.asset_urls_for(
                        result.special_type, result.special_asset_stems
                    ),
                    "counts_as_cooking": result.counts_as_cooking,
                }
                for position, result in enumerate(results, 1)
            ],
            "summary": await self.repository.profile(visitor_id, recent_limit=0),
        }
