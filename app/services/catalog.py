from __future__ import annotations

import hashlib
import json
import logging
import math
from pathlib import Path
import threading

from app.domain.models import CatalogSnapshot, EggCategory, SpecialOutcome


logger = logging.getLogger(__name__)
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")


class CatalogError(RuntimeError):
    pass


class CatalogService:
    def __init__(self, content_dir: Path) -> None:
        self.content_dir = content_dir.resolve()
        self._snapshot: CatalogSnapshot | None = None
        self._signature: tuple[tuple[str, int, int], ...] | None = None
        self._lock = threading.Lock()

    @property
    def snapshot(self) -> CatalogSnapshot:
        if self._snapshot is None:
            return self.load()
        self.reload_if_changed()
        return self._snapshot

    def _content_signature(self) -> tuple[tuple[str, int, int], ...]:
        paths = [self.content_dir / "paro_pools.json", self.content_dir / "paro_config.json"]
        image_root = self.content_dir / "images" / "paro_avatars"
        if image_root.exists():
            paths.extend(path for path in image_root.rglob("*") if path.is_file())
        return tuple(
            sorted(
                (str(path.relative_to(self.content_dir)), path.stat().st_mtime_ns, path.stat().st_size)
                for path in paths
                if path.exists()
            )
        )

    def load(self) -> CatalogSnapshot:
        with self._lock:
            snapshot = self._load_validated()
            self._snapshot = snapshot
            self._signature = self._content_signature()
            return snapshot

    def reload_if_changed(self) -> bool:
        signature = self._content_signature()
        if signature == self._signature:
            return False
        with self._lock:
            if signature == self._signature:
                return False
            try:
                snapshot = self._load_validated()
            except Exception:
                logger.exception("Content reload failed; keeping the previous valid snapshot")
                return False
            self._snapshot = snapshot
            self._signature = signature
            return True

    def _load_validated(self) -> CatalogSnapshot:
        pools_bytes = (self.content_dir / "paro_pools.json").read_bytes()
        config_bytes = (self.content_dir / "paro_config.json").read_bytes()
        try:
            pools = json.loads(pools_bytes)
            config = json.loads(config_bytes)
        except json.JSONDecodeError as exc:
            raise CatalogError("内容 JSON 无法解析") from exc

        akito_pool = self._validate_pool(pools, "akito_pool")
        toya_pool = self._validate_pool(pools, "toya_pool")
        categories = self._validate_categories(config)
        outcomes = tuple(
            outcome
            for category in categories
            for outcome in category.outcomes
        )
        avatars = {
            "akito": {name: self._avatar_url("彰人", name) for name in akito_pool},
            "toya": {name: self._avatar_url("冬弥", name) for name in toya_pool},
        }
        special_assets = {
            outcome.id: tuple(
                url
                for asset in outcome.assets
                if (url := self._asset_url("fox&rabbit", asset)) is not None
            )
            for outcome in outcomes
        }
        digest = hashlib.sha256(pools_bytes + b"\0" + config_bytes).hexdigest()[:16]
        return CatalogSnapshot(
            version=digest,
            akito_pool=akito_pool,
            toya_pool=toya_pool,
            egg_categories=categories,
            avatars=avatars,
            special_assets=special_assets,
        )

    @staticmethod
    def _validate_pool(pools: object, key: str) -> tuple[str, ...]:
        if not isinstance(pools, dict) or not isinstance(pools.get(key), list):
            raise CatalogError(f"{key} 必须是列表")
        values = tuple(str(value).strip() for value in pools[key])
        if not values or any(not value for value in values) or len(values) != len(set(values)):
            raise CatalogError(f"{key} 不能为空、含空项或重复项")
        return values

    @staticmethod
    def _validate_rate(value: object, key: str) -> float:
        try:
            rate = float(value)
        except (TypeError, ValueError) as exc:
            raise CatalogError(f"{key} 必须是数字") from exc
        if not math.isfinite(rate) or not 0 <= rate <= 1:
            raise CatalogError(f"{key} 必须在 0 到 1 之间")
        return rate

    @staticmethod
    def _validate_priority(value: object, category_id: str) -> int:
        # 与 Bot 的读取方式对齐：缺省按 0 处理，整数写法的浮点（如 100.0）也照收，
        # 避免以后 Bot 侧改成浮点后整份配置被拒、网页却静默停留在旧概率上。
        if value is None:
            return 0
        if isinstance(value, bool):
            raise CatalogError(f"彩蛋大类 {category_id} 的 priority 必须是数字")
        try:
            priority = float(value)
        except (TypeError, ValueError) as exc:
            raise CatalogError(f"彩蛋大类 {category_id} 的 priority 必须是数字") from exc
        if not math.isfinite(priority) or not priority.is_integer():
            raise CatalogError(f"彩蛋大类 {category_id} 的 priority 必须是整数")
        return int(priority)

    @classmethod
    def _validate_categories(cls, config: object) -> tuple[EggCategory, ...]:
        if not isinstance(config, dict):
            raise CatalogError("paro_config.json 必须是对象")
        raw_categories = config.get("egg_categories")
        if raw_categories is None:
            raw_categories = [
                {
                    "id": "foxrabbit",
                    "rate": config.get("special_rate"),
                    "priority": 100,
                    "result_type": "special",
                    "outcomes": config.get("special_outcomes"),
                },
                {
                    "id": "cooking",
                    "rate": config.get("cooking_rate"),
                    "priority": 0,
                    "result_type": "cooking",
                },
            ]
        if not isinstance(raw_categories, list) or not raw_categories:
            raise CatalogError("egg_categories 必须是非空列表")

        categories: list[EggCategory] = []
        seen_category_ids: set[str] = set()
        seen_outcome_ids: set[str] = set()
        for item in raw_categories:
            if not isinstance(item, dict):
                raise CatalogError("彩蛋大类必须是对象")
            category_id = str(item.get("id", "")).strip()
            if not category_id or category_id in seen_category_ids:
                raise CatalogError("彩蛋大类 ID 不能为空或重复")
            result_type = item.get("result_type")
            if result_type not in {"special", "cooking"}:
                raise CatalogError("彩蛋大类 result_type 必须是 special 或 cooking")
            priority = cls._validate_priority(item.get("priority"), category_id)
            rate = cls._validate_rate(item.get("rate"), f"彩蛋大类 {category_id} 的 rate")
            outcomes = (
                cls._validate_outcomes(item.get("outcomes"), category_id, seen_outcome_ids)
                if result_type == "special"
                else ()
            )
            categories.append(
                EggCategory(
                    id=category_id,
                    rate=rate,
                    priority=priority,
                    result_type=result_type,
                    outcomes=outcomes,
                )
            )
            seen_category_ids.add(category_id)

        if math.fsum(category.rate for category in categories) > 1:
            raise CatalogError("彩蛋大类概率之和不能超过 1")
        return tuple(categories)

    @staticmethod
    def _validate_outcomes(
        raw: object,
        category_id: str,
        seen_ids: set[str],
    ) -> tuple[SpecialOutcome, ...]:
        if not isinstance(raw, list) or not raw:
            raise CatalogError(f"特殊彩蛋大类 {category_id} 的 outcomes 必须是非空列表")
        outcomes: list[SpecialOutcome] = []
        for item in raw:
            if not isinstance(item, dict):
                raise CatalogError("特殊结果必须是对象")
            outcome_id = str(item.get("id", "")).strip()
            if not outcome_id or outcome_id in seen_ids:
                raise CatalogError("特殊结果 ID 不能为空或重复")
            try:
                weight = float(item.get("weight"))
            except (TypeError, ValueError) as exc:
                raise CatalogError("特殊结果权重必须是数字") from exc
            raw_tags = item.get("tags", [])
            raw_assets = item.get("assets", [])
            if not isinstance(raw_tags, list) or not isinstance(raw_assets, list):
                raise CatalogError("特殊结果 tags 和 assets 必须是列表")
            tags = tuple(str(tag).strip() for tag in raw_tags if str(tag).strip())
            assets = tuple(str(asset).strip() for asset in raw_assets if str(asset).strip())
            if not math.isfinite(weight) or weight <= 0:
                raise CatalogError("特殊结果权重必须是有限正数")
            outcomes.append(
                SpecialOutcome(
                    id=outcome_id,
                    category_id=category_id,
                    label=str(item.get("label") or outcome_id),
                    weight=weight,
                    tags=tags,
                    assets=assets,
                    message=str(item.get("message") or item.get("label") or outcome_id),
                    counts_as_cooking=bool(item.get("counts_as_cooking", False)),
                )
            )
            seen_ids.add(outcome_id)
        return tuple(outcomes)

    def _avatar_url(self, character: str, name: str) -> str | None:
        return self._asset_url(character, name)

    def _asset_url(self, folder: str, stem: str) -> str | None:
        image_root = (self.content_dir / "images" / "paro_avatars").resolve()
        for extension in IMAGE_EXTENSIONS:
            path = (image_root / folder / f"{stem}{extension}").resolve()
            try:
                relative = path.relative_to(image_root)
            except ValueError as exc:
                raise CatalogError("素材路径超出允许目录") from exc
            if path.is_file():
                # 把文件 mtime 当版本号拼进 URL：换图 → URL 变化 → 浏览器必然重新拉取；
                # 没换的图 URL 不变，沿用它原有的缓存行为。
                # 不加这个参数时，同名覆盖图片会因为 URL 完全相同而被浏览器（及其
                # 启发式新鲜期，=(now - Last-Modified) * 10%，老图可长达十几天）一直吃缓存，
                # 导致换了图但老访客看不到。
                stamp = path.stat().st_mtime_ns
                return "/content/" + "/".join(relative.parts) + f"?v={stamp}"
        logger.warning("Missing content image: %s/%s", folder, stem)
        return None
