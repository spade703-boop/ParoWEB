from __future__ import annotations

import glob
import hashlib
import json
import logging
import math
from pathlib import Path
import re
import threading

from app.domain.models import CatalogSnapshot, EggCategory, SpecialOutcome


logger = logging.getLogger(__name__)
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")

# 多图轮换：主图 `狐&兔` + 变体 `狐&兔_2` / `狐&兔_3`，与 Bot 侧命名规则一致。
_VARIANT_RE = re.compile(r"^(?P<base>.+?)(?:_(?P<index>\d+))?$")


def _variant_sort_key(stem: str) -> tuple[int, int, str]:
    """主图排最前，变体按下划线后的数字升序（_2 在 _10 前，不是字典序）。"""
    matched = _VARIANT_RE.fullmatch(stem)
    index = matched.group("index") if matched else None
    return (0, 0, stem) if index is None else (1, int(index), stem)


class CatalogError(RuntimeError):
    pass


class CatalogService:
    def __init__(self, content_dir: Path, images_dir: Path | None = None) -> None:
        self.content_dir = content_dir.resolve()
        # 图片可以和 JSON 分开放（Bot 把 paro_pools/config 挪进了 data/paro/，
        # 头像仍留在 data/images/），不单独指定时沿用旧布局 content_dir/images。
        self.images_dir = (images_dir or content_dir / "images").resolve()
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
        image_root = self.images_dir / "paro_avatars"
        if image_root.exists():
            paths.extend(path for path in image_root.rglob("*") if path.is_file())
        return tuple(
            sorted(
                (str(path), path.stat().st_mtime_ns, path.stat().st_size)
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
                if (url := self._asset_url(outcome.asset_dir, asset)) is not None
            )
            for outcome in outcomes
        }
        special_variant_urls = {
            outcome.id: self._stem_urls(outcome.asset_dir, outcome.assets)
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
            special_variant_urls=special_variant_urls,
        )

    def _stem_urls(
        self, folder: str, assets: tuple[str, ...]
    ) -> dict[str, dict[str, str]]:
        """按 assets 槽位分组收候选图：{槽位: {stem: URL}}，主图在最前。"""
        grouped: dict[str, dict[str, str]] = {}
        for asset in assets:
            urls: dict[str, str] = {}
            for stem in self._variant_stems(folder, asset):
                url = self._asset_url(folder, stem)
                if url is not None:
                    urls[stem] = url
            grouped[asset] = urls
        return grouped

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
            asset_dir = str(item.get("asset_dir") or "fox&rabbit").strip()
            if (
                not asset_dir
                or asset_dir in {".", ".."}
                or any(character in asset_dir for character in '<>:"/\\|?*')
            ):
                raise CatalogError(f"彩蛋大类 {category_id} 的 asset_dir 不是合法目录名")
            raw_accents = item.get("accents", {})
            if not isinstance(raw_accents, dict):
                raise CatalogError(f"彩蛋大类 {category_id} 的 accents 必须是对象")
            accents = {
                str(tag).strip(): str(accent).strip()
                for tag, accent in raw_accents.items()
                if str(tag).strip()
            }
            if any(accent not in {"a", "t"} for accent in accents.values()):
                raise CatalogError(f"彩蛋大类 {category_id} 的 accents 只支持 a 或 t")
            outcomes = (
                cls._validate_outcomes(
                    item.get("outcomes"),
                    category_id,
                    seen_outcome_ids,
                    asset_dir=asset_dir,
                    accents=accents,
                )
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
        *,
        asset_dir: str,
        accents: dict[str, str],
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
            raw_layout = item.get("layout")
            if raw_layout is None:
                layout = (
                    "pair"
                    if len(assets) > 1
                    else "wide"
                    if len(assets) == 1 and "&" in assets[0]
                    else "single"
                )
            else:
                layout = str(raw_layout).strip()
            if layout not in {"single", "pair", "wide"}:
                raise CatalogError("特殊结果 layout 必须是 single、pair 或 wide")
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
                    unique_per_draw=bool(item.get("unique_per_draw", False)),
                    asset_dir=asset_dir,
                    layout=layout,
                    accents=dict(accents),
                )
            )
            seen_ids.add(outcome_id)
        return tuple(outcomes)

    def _avatar_url(self, character: str, name: str) -> str | None:
        return self._asset_url(character, name)

    def _variant_stems(self, folder: str, base: str) -> tuple[str, ...]:
        """列出 `base` 的全部候选图 stem：主图在前，变体按数字升序（_2 在 _10 前）。"""
        image_root = (self.images_dir / "paro_avatars").resolve()
        target = (image_root / folder).resolve()
        try:
            target.relative_to(image_root)
        except ValueError as exc:
            raise CatalogError("素材路径超出允许目录") from exc
        if not target.is_dir():
            return ()
        found: dict[str, Path] = {}
        for extension in IMAGE_EXTENSIONS:
            for candidate in target.glob(f"{glob.escape(base)}*{extension}"):
                matched = _VARIANT_RE.fullmatch(candidate.stem)
                if not matched or matched.group("base") != base:
                    continue
                if candidate.stat().st_size <= 0:
                    continue
                found.setdefault(candidate.stem, candidate)
        return tuple(sorted(found, key=_variant_sort_key))

    def _asset_url(self, folder: str, stem: str) -> str | None:
        image_root = (self.images_dir / "paro_avatars").resolve()
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
