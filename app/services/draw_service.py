from __future__ import annotations

from app.domain.draw import draw_results, resolve_fixed_name
from app.domain.models import FixedSide
from app.repositories.sqlite import SQLiteRepository
from app.services.catalog import CatalogService


class DrawService:
    def __init__(self, catalog: CatalogService, repository: SQLiteRepository) -> None:
        self.catalog = catalog
        self.repository = repository

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
        batch_id, created_at = await self.repository.create_draw(
            visitor_id=visitor_id,
            requested_count=count,
            fixed_side=fixed_side,
            fixed_name=resolved_fixed_name,
            results=results,
        )
        outcome_map = snapshot.outcome_map()
        return {
            "batch_id": batch_id,
            "created_at": created_at,
            "results": [
                {
                    "position": position,
                    "akito_name": result.akito_name,
                    "toya_name": result.toya_name,
                    "akito_avatar_url": snapshot.avatars["akito"].get(result.akito_name) if result.akito_name else None,
                    "toya_avatar_url": snapshot.avatars["toya"].get(result.toya_name) if result.toya_name else None,
                    "is_cooking": result.is_cooking,
                    "special_type": result.special_type,
                    "special_label": outcome_map[result.special_type].label if result.special_type else None,
                    "special_message": outcome_map[result.special_type].message if result.special_type else None,
                    "special_asset_urls": list(snapshot.special_assets.get(result.special_type, ())) if result.special_type else [],
                    "counts_as_cooking": result.counts_as_cooking,
                }
                for position, result in enumerate(results, 1)
            ],
            "summary": await self.repository.profile(visitor_id, recent_limit=0),
        }
