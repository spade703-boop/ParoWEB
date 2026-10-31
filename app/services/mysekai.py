"""MySekai 群友入口：密码会话、Haruki 绑定资料、出图编排。

设计边界（2026-10-21 与用户确认）：

* 绑定资料来自 Haruki 工具箱公开 API：
  ``GET /api/public/{server}/suite/{game_id}?key=userGamedata,userProfile,userDecks,userCards``
  无需 token；用户需在 Haruki 侧开启公开权限。
* 昵称取 ``userGamedata.name``，头像取 ``userGamedata.deck`` 对应 deck 的 leader 卡，
  再经 ``cards.json`` 的 ``assetbundleName`` 拼 ``character/member/.../card_*.png``。
  这与 Haruki-Drawing-API 的 ``_profile_card`` / ``_leader_image_path`` 一致。
* 网页上传只收响应体；服务端不能从响应体证明它属于某个游戏 ID，所以这里只做
  「QQ 已绑定 + 用户填写的 ID 与绑定一致」的校验，并记录响应体 SHA-256 供核对。
* 请求体只在内存中使用；落盘的只有公开的绑定资料与审计元数据。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import io
import json
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from weakref import WeakValueDictionary

import httpx
from PIL import Image

from app.config import Settings
from app.repositories.sqlite import SQLiteRepository
from app.services.mysekai_window import (
    FLOWER_WINDOW,
    birthday_dates,
    current_flower_window,
    game_day_key,
    is_exempt,
    watch_is_open,
    watch_opens_at,
    window_key,
)

logger = logging.getLogger(__name__)

COOKIE_NAME = "paro_mysekai"
SESSION_TTL_SECONDS = 30 * 24 * 60 * 60
MAX_BODY_BYTES = 4 * 1024 * 1024
HIGHLIGHT_LIMIT = 20
#: 一个 QQ 最多绑几个游戏角色；群指令用 u1..u3 指代。
MAX_ROLES_PER_QQ = 3
AVATAR_SIZE = 96
CARDS_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60

#: 每条指令实际请求的 suite 字段。``userGamedata`` 与 ``upload_time`` 必须存在：
#: 单 key 读不保证返回对象，且版本号是缓存失效的唯一依据（见 Haruki-Cloud
#: ``render/snapshot/suite_fields.go``）。
SUITE_PROJECTIONS: dict[str, str] = {
    "fixture-list": "userMysekaiBlueprints,userGamedata,upload_time",
    # 只放实测可用的键：userMysekaiBlueprints 与 userMysekaiGamedata 都不在公开
    # 白名单里，带上任何一个都会让整组 key 404。出图侧已支持蓝图状态未知。
    "talk-list": "userMysekaiCharacterTalks,userGamedata,upload_time",
    "music-record": "userMysekaiMusicRecords,userGamedata,upload_time",
}

#: 出图服务的新路由；与 ``/render/gate`` 同为「JSON 进、PNG 出」。
CATALOG_ENDPOINTS: dict[str, str] = {
    "fixture-list": "/render/mysekai/fixture-list",
    "talk-list": "/render/mysekai/talk-list",
    "music-record": "/render/mysekai/music-record",
    "convert": "/render/mysekai/convert",
}


class MysekaiError(Exception):
    """A user-facing MySekai failure; the API layer turns it into JSON."""

    code = "mysekai_error"
    status = 400

    def __init__(
        self,
        message: str,
        *,
        details: dict | None = None,
        code: str | None = None,
        status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.details = details or {}
        if code is not None:
            self.code = code
        if status is not None:
            self.status = status


class MysekaiDisabled(MysekaiError):
    code = "mysekai_disabled"
    status = 503


class InvalidPassword(MysekaiError):
    code = "invalid_password"
    status = 401


class SessionInvalid(MysekaiError):
    code = "session_invalid"
    status = 401


class BindingNotFound(MysekaiError):
    code = "not_bound"
    status = 403


class BindingMismatch(MysekaiError):
    code = "binding_mismatch"
    status = 403


class BindingConflict(MysekaiError):
    code = "binding_conflict"
    status = 409


class AccountNotFound(MysekaiError):
    code = "account_not_found"
    status = 404


class UpstreamError(MysekaiError):
    code = "upstream_error"
    status = 502


class HarukiUnavailable(UpstreamError):
    code = "haruki_unavailable"


class GateUnitUnknown(MysekaiError):
    """The requested team token is not one of the gate aliases."""

    code = "gate_unit_unknown"
    status = 422


class GateNotFound(MysekaiError):
    """The account has no row for the requested gate."""

    code = "gate_not_found"
    status = 404


class GateMaxLevel(MysekaiError):
    """The requested gate has no level left to upgrade."""

    code = "gate_max_level"
    status = 409


class CharacterUnknown(MysekaiError):
    """The typed character alias is not in the nickname table."""

    code = "character_unknown"
    status = 422


class VirtualSingerUnitRequired(MysekaiError):
    """A virtual singer was typed without the unit that picks its version."""

    code = "virtual_singer_unit_required"
    status = 422


class UnitUnknown(MysekaiError):
    """The character has no version for the typed unit token."""

    code = "unit_unknown"
    status = 422


class CatalogStateMissing(MysekaiError):
    """The account has no stored capture state, or it lacks the needed resource."""

    code = "catalog_state_missing"
    status = 409


class CaptureStale(MysekaiError):
    """The account's latest snapshot does not carry the resource this needs."""

    code = "capture_stale"
    status = 409


#: Gate id per team. Latin tokens match case-insensitively; the official
#: short Chinese names are accepted alongside them.
GATE_UNIT_ALIASES: dict[str, int] = {
    "ln": 1,
    "教室": 1,
    "mmj": 2,
    "舞台": 2,
    "vbs": 3,
    "街头": 3,
    "ws": 4,
    "奇幻乐园": 4,
    "25": 5,
    "25h": 5,
    "25时": 5,
    "无人": 5,
}
GATE_UNIT_HINT = "可用写法：ln / mmj / vbs / ws / 25时，或教室、舞台、街头、奇幻乐园、无人"


def resolve_gate_unit(token: str | None) -> int | None:
    """Map a command token to a gate id; ``None`` when no token was given."""
    if token is None:
        return None
    key = token.strip().casefold()
    if not key:
        return None
    gate_id = GATE_UNIT_ALIASES.get(key)
    if gate_id is None:
        raise GateUnitUnknown(f"不认识这个团队：{token}。{GATE_UNIT_HINT}")
    return gate_id


@dataclass(frozen=True, slots=True)
class HarukiProfile:
    game_user_id: str
    nickname: str | None
    deck_id: int | None
    leader_card_id: int | None
    profile_image_type: str | None
    avatar_png: bytes | None
    #: Haruki 上这份 suite 的更新时间（响应里的 upload_time）
    suite_uploaded_at_ms: int | None = None


@dataclass(frozen=True, slots=True)
class RenderResult:
    sites: list[dict]
    points: int
    drops: int
    map_png_base64: str
    card_png_base64: str
    visit_counts: dict[str, int]
    birthday: dict | None = None
    catalog_state: dict | None = None
    kind: str = "snapshot"
    rare_png_base64: str | None = None
    birthday_png_base64: str | None = None
    message: str | None = None


_CN = timezone(timedelta(hours=8))
#: JP 7.0.0 raised the gate cap from 40 to 70; the render side reads the real cap
#: from masterdata, this constant only decides which gate Haruki would default to.
GATE_MAX_LEVEL = 70


class RenderCache:
    """Rendered PNGs keyed by the exact render body, bounded by total bytes.

    The body already carries the player's data version (``upload_time`` and the
    profile fields), so a re-upload changes the key; no TTL is needed. A repeat
    command becomes a dict lookup instead of a full re-render, and concurrent
    requests for the same body share one render.
    """

    def __init__(self, max_bytes: int = 128 * 1024 * 1024) -> None:
        self.max_bytes = max_bytes
        self._entries: OrderedDict[str, bytes] = OrderedDict()
        #: Locks are weak: the dict shrinks once no request holds one.
        self._inflight: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
        self._size = 0

    @staticmethod
    def key(payload: dict) -> str:
        """A stable digest of one render body, independent of dict order."""
        canonical = json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def get(self, key: str) -> bytes | None:
        png = self._entries.get(key)
        if png is not None:
            self._entries.move_to_end(key)
        return png

    async def get_or_render(self, key: str, render) -> bytes:
        """Return the cached PNG, or render it once for every waiter."""
        cached = self.get(key)
        if cached is not None:
            return cached
        lock = self._inflight.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._inflight[key] = lock
        async with lock:
            cached = self.get(key)
            if cached is not None:
                return cached
            png = await render()
            self._store(key, png)
            return png

    def _store(self, key: str, png: bytes) -> None:
        dropped = self._entries.pop(key, None)
        if dropped is not None:
            self._size -= len(dropped)
        self._entries[key] = png
        self._size += len(png)
        # Keep at least the entry just written, even if it alone exceeds the cap.
        while self._size > self.max_bytes and len(self._entries) > 1:
            _, evicted = self._entries.popitem(last=False)
            self._size -= len(evicted)

    def clear(self) -> None:
        self._entries.clear()
        self._size = 0


@dataclass(frozen=True)
class MysekaiGateData:
    """One gate's current level plus the player's material holdings."""

    gate_id: int
    gate_level: int
    materials: dict[int, int]
    fallback_used: bool


def _game_day_key() -> str:
    """The game day under UTC+8, used as the visit-baseline partition.

    记忆每个角色每天只能拿一份，05:00 清零；自然日的 00:00 分界比游戏早
    5 小时，按自然日记会错（2026-10-31）。
    """
    return game_day_key(datetime.now(_CN))


def _b64url_encode(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _error_message(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    if isinstance(payload, dict):
        value = payload.get("error")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


class MysekaiService:
    def __init__(
        self,
        settings: Settings,
        repository: SQLiteRepository,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.transport = transport
        self._card_master: dict[str, dict[int, str]] = {}
        #: (server, game_id, projection) -> (upload_time, payload); the version
        #: is what makes a repeat command a one-hop probe instead of a refetch.
        self._suite_cache: dict[tuple[str, str, str], tuple[int | None, dict]] = {}
        #: Rendered catalog PNGs; a repeat command never reaches the renderer.
        self.render_cache = RenderCache()
        #: The render service's material table; refreshed on master updates.
        self._materials_cache: list[dict] | None = None
        #: The material icon/name sheet PNG; static until the master changes.
        self._material_card_cache: bytes | None = None

    # ------------------------------------------------------------------ session
    def enabled(self) -> bool:
        return bool(self.settings.mysekai_password)

    def _now(self) -> datetime:
        """Current time in the game's timezone; overridable in tests."""
        return datetime.now(_CN)

    def verify_password(self, password: str) -> None:
        if not self.enabled():
            raise MysekaiDisabled("入口还没有开启（未配置访问密码）")
        expected = self.settings.mysekai_password.encode("utf-8")
        actual = password.encode("utf-8")
        if not hmac.compare_digest(actual, expected):
            raise InvalidPassword("密码不正确")

    def _password_fingerprint(self) -> str:
        """Short digest of the current password, bound into every session token.

        Changing ``PARO_MYSEKAI_PASSWORD`` changes this value, so tokens issued
        before the change stop verifying without touching the session secret.
        """
        digest = hmac.new(
            self.settings.mysekai_session_secret.encode("utf-8"),
            self.settings.mysekai_password.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return digest[:16]

    def issue_token(self) -> str:
        payload = json.dumps(
            {
                "exp": int(time.time()) + SESSION_TTL_SECONDS,
                "pw": self._password_fingerprint(),
            },
            separators=(",", ":"),
        ).encode("utf-8")
        body = _b64url_encode(payload)
        signature = hmac.new(
            self.settings.mysekai_session_secret.encode("utf-8"),
            body.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        return f"{body}.{signature}"

    def verify_token(self, token: str | None) -> bool:
        if not token or "." not in token:
            return False
        body, _, signature = token.rpartition(".")
        expected = hmac.new(
            self.settings.mysekai_session_secret.encode("utf-8"),
            body.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return False
        try:
            payload = json.loads(_b64url_decode(body))
            if not hmac.compare_digest(
                str(payload.get("pw", "")), self._password_fingerprint()
            ):
                return False
            return int(payload.get("exp", 0)) >= int(time.time())
        except (ValueError, TypeError):
            return False

    # ------------------------------------------------------------------ HTTP
    def _client(self, *, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self.transport,
            timeout=httpx.Timeout(timeout),
            follow_redirects=True,
            headers={"User-Agent": "ParoWEB-mysekai/1.0"},
        )

    async def _card_assetbundle(self, card_id: int, server: str) -> str | None:
        master = await self._load_card_master(server)
        return master.get(card_id)

    async def _load_card_master(self, server: str) -> dict[int, str]:
        cached = self._card_master.get(server)
        if cached is not None:
            return cached
        cache_path = (
            self.settings.database_path.parent
            / "mysekai"
            / f"cards-{server}.json"
        )
        data = None
        if cache_path.exists():
            try:
                if time.time() - cache_path.stat().st_mtime < CARDS_CACHE_TTL_SECONDS:
                    data = json.loads(cache_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = None
        if data is None:
            url = (
                f"{self.settings.mysekai_master_base}/v1/master/"
                f"{server}/files/cards.json"
            )
            async with self._client(timeout=30.0) as client:
                response = await client.get(url)
            if response.status_code != 200:
                raise UpstreamError("卡牌主数据暂时不可用")
            data = response.json()
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(
                    json.dumps(data, ensure_ascii=False),
                    encoding="utf-8",
                )
            except OSError:
                pass
        rows = data if isinstance(data, list) else data.get("cards") or []
        master = {
            int(row["id"]): str(row["assetbundleName"])
            for row in rows
            if isinstance(row, dict) and row.get("id") is not None and row.get("assetbundleName")
        }
        self._card_master[server] = master
        return master

    async def _download_avatar(
        self,
        *,
        assetbundle_name: str,
        trained: bool,
        server: str,
    ) -> bytes | None:
        suffix = "after_training" if trained else "normal"
        prefix = "cn" if server == "cn" else server
        base = f"{self.settings.mysekai_asset_base}/{prefix}-assets"
        # Haruki-Drawing-API 的 _leader_image_path 用的是
        # thumbnail/chara/{assetbundleName}_{normal|after_training}.png（128×128）。
        # 整张卡面 character/member/.../card_*.png 是后备，不是首选。
        candidates = (
            f"{base}/startapp/thumbnail/chara/{assetbundle_name}_{suffix}.png",
            f"{base}/ondemand/thumbnail/chara/{assetbundle_name}_{suffix}.png",
            f"{base}/startapp/character/member/{assetbundle_name}/card_{suffix}.png",
        )
        content = None
        try:
            async with self._client(timeout=30.0) as client:
                for url in candidates:
                    response = await client.get(url)
                    if response.status_code == 200:
                        content = response.content
                        break
        except httpx.HTTPError:
            return None
        if not content:
            return None
        try:
            image = Image.open(io.BytesIO(content)).convert("RGBA")
        except OSError:
            return None
        scale = max(AVATAR_SIZE / image.width, AVATAR_SIZE / image.height)
        resized = image.resize(
            (
                max(1, round(image.width * scale)),
                max(1, round(image.height * scale)),
            ),
            Image.Resampling.LANCZOS,
        )
        left = (resized.width - AVATAR_SIZE) // 2
        top = (resized.height - AVATAR_SIZE) // 2
        cropped = resized.crop(
            (left, top, left + AVATAR_SIZE, top + AVATAR_SIZE)
        )
        buffer = io.BytesIO()
        cropped.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()

    async def fetch_haruki_profile(
        self, game_user_id: str, server: str
    ) -> HarukiProfile:
        if not game_user_id.isdigit():
            raise AccountNotFound("游戏 ID 必须是数字")
        url = (
            f"{self.settings.mysekai_haruki_base}/api/public/"
            f"{server}/suite/{game_user_id}"
        )
        params = {"key": "userGamedata,userProfile,userDecks,userCards"}
        try:
            async with self._client(timeout=20.0) as client:
                response = await client.get(url, params=params)
        except httpx.HTTPError:
            raise HarukiUnavailable("Haruki 暂时不可用，请稍后再试") from None
        if response.status_code == 404:
            raise AccountNotFound(
                "游戏 ID 不存在，或还没有在 Haruki 开启公开权限"
            )
        if response.status_code >= 500:
            raise HarukiUnavailable("Haruki 暂时不可用，请稍后再试")
        if response.status_code != 200:
            raise UpstreamError("Haruki 暂时不可用，请稍后再试")
        try:
            data = response.json()
        except ValueError:
            raise UpstreamError("Haruki 返回了无法解析的数据") from None
        if not isinstance(data, dict):
            raise UpstreamError("Haruki 返回了无法解析的数据")
        gamedata = data.get("userGamedata") or {}
        user_profile = data.get("userProfile") or {}
        # 顺手把游戏等级落库：抓包只有烤森等级，卡片的 Lv chip 靠这一次。
        await self.remember_game_rank(server, game_user_id, gamedata.get("rank"))
        returned_id = str(
            gamedata.get("userId")
            or user_profile.get("userId")
            or ""
        )
        if returned_id != game_user_id:
            raise UpstreamError("Haruki 返回的账号与请求不一致")
        suite_uploaded_at = _capture_ms(data.get("upload_time"))
        nickname = str(gamedata.get("name") or "").strip() or None
        deck_id = gamedata.get("deck")
        decks = data.get("userDecks") or []
        current_deck = None
        if isinstance(decks, list) and decks:
            current_deck = next(
                (
                    deck
                    for deck in decks
                    if deck_id is not None and deck.get("deckId") == deck_id
                ),
                decks[0],
            )
        leader_card_id = None
        if isinstance(current_deck, dict) and current_deck.get("leader") is not None:
            try:
                leader_card_id = int(current_deck["leader"])
            except (TypeError, ValueError):
                leader_card_id = None
        trained = False
        if leader_card_id is not None:
            for card in data.get("userCards") or []:
                if isinstance(card, dict) and card.get("cardId") == leader_card_id:
                    trained = card.get("defaultImage") == "special_training"
                    break
        assetbundle = None
        if leader_card_id is not None:
            try:
                assetbundle = await self._card_assetbundle(leader_card_id, server)
            except (httpx.HTTPError, UpstreamError, ValueError):
                assetbundle = None
        avatar_png = None
        if assetbundle:
            avatar_png = await self._download_avatar(
                assetbundle_name=assetbundle,
                trained=trained,
                server=server,
            )
        return HarukiProfile(
            game_user_id=game_user_id,
            nickname=nickname,
            deck_id=int(deck_id) if isinstance(deck_id, int) else None,
            leader_card_id=leader_card_id,
            profile_image_type=user_profile.get("profileImageType"),
            avatar_png=avatar_png,
            suite_uploaded_at_ms=suite_uploaded_at,
        )

    async def fetch_mysekai_gate_data(
        self,
        game_user_id: str,
        server: str = "cn",
        *,
        requested_gate_id: int | None = None,
    ) -> MysekaiGateData:
        """Read the gate level and material holdings from Haruki's public suite.

        Only the two arrays the gate image needs are requested. ``userMysekai``
        as a parent key is rejected with 404, but the concrete field names are
        allowed; if a server's allow-list rejects them anyway, this falls back
        to the whole suite once.
        """
        if not game_user_id.isdigit():
            raise AccountNotFound("游戏 ID 必须是数字")
        url = (
            f"{self.settings.mysekai_haruki_base}/api/public/"
            f"{server}/suite/{game_user_id}"
        )
        params = {"key": "userMysekaiGates,userMysekaiMaterials"}
        try:
            async with self._client(timeout=60.0) as client:
                response = await client.get(url, params=params)
                if response.status_code == 404:
                    response = await client.get(url)
        except httpx.HTTPError:
            raise HarukiUnavailable("Haruki 暂时不可用，请稍后再试") from None
        if response.status_code == 404:
            raise AccountNotFound("游戏 ID 不存在，或还没有在 Haruki 开启公开权限")
        if response.status_code >= 500:
            raise HarukiUnavailable("Haruki 暂时不可用，请稍后再试")
        if response.status_code != 200:
            raise UpstreamError("Haruki 暂时不可用，请稍后再试")
        try:
            data = response.json()
        except ValueError:
            raise UpstreamError("Haruki 返回了无法解析的数据") from None
        if not isinstance(data, dict):
            raise UpstreamError("Haruki 返回了无法解析的数据")

        gates = [g for g in (data.get("userMysekaiGates") or []) if isinstance(g, dict)]
        levels = {
            int(g["mysekaiGateId"]): int(g.get("mysekaiGateLevel") or 0)
            for g in gates
            if g.get("mysekaiGateId")
        }
        # Haruki 的默认选择：等级最高但还没满级的门，并列时保留较小的 gate id
        if requested_gate_id is not None:
            if requested_gate_id not in levels:
                raise GateNotFound(
                    "你的账号里还没有这个门的数据，先确认选的是哪个团队"
                )
            chosen_id = requested_gate_id
            fallback_used = False
        else:
            eligible = [
                (gate_id, level)
                for gate_id, level in sorted(levels.items())
                if 0 < level < GATE_MAX_LEVEL
            ]
            fallback_used = not eligible
            chosen_id = (
                max(eligible, key=lambda item: item[1])[0]
                if eligible
                else (min(levels) if levels else 0)
            )
        current = next(
            (g for g in gates if int(g["mysekaiGateId"]) == chosen_id), None
        )
        if not isinstance(current, dict):
            raise UpstreamError("Haruki 没有返回大门数据")

        materials: dict[int, int] = {}
        for item in data.get("userMysekaiMaterials") or []:
            if not isinstance(item, dict):
                continue
            materials[int(item["mysekaiMaterialId"])] = int(item["quantity"])
        return MysekaiGateData(
            gate_id=int(current["mysekaiGateId"]),
            gate_level=int(current["mysekaiGateLevel"]),
            materials=materials,
            fallback_used=fallback_used,
        )

    async def render_gate_image(
        self, data: MysekaiGateData, profile: dict | None = None
    ) -> bytes:
        """Ask the render service for the gate upgrade long image."""
        payload = {
            "gate_id": data.gate_id,
            "level": data.gate_level,
            "materials": {str(k): v for k, v in data.materials.items()},
        }
        if profile:
            payload["profile"] = profile
        url = f"{self.settings.mysekai_service_url}/render/gate"
        try:
            async with self._client(timeout=60.0) as client:
                response = await client.post(url, json=payload)
        except httpx.HTTPError:
            raise UpstreamError("出图服务暂时不可用，请稍后再试") from None
        if response.status_code == 409:
            # The render service reports an empty plan (already at the master cap).
            raise GateMaxLevel("这个门已经满级了，没有可升级的等级")
        if response.status_code != 200:
            raise UpstreamError("出图服务暂时不可用，请稍后再试")
        return response.content

    async def fetch_mysekai_suite(
        self, game_user_id: str, kind: str, server: str = "cn"
    ) -> dict:
        """Read one projected suite slice, refetching only when the version moved.

        The ``upload_time`` probe costs a few hundred bytes; the projection is
        fetched only when it disagrees with the cached version. A 404 means the
        account's snapshot lacks the requested resource -- it does not fall back
        to the whole suite, because that is megabytes and would fail the same way.
        """
        if not game_user_id.isdigit():
            raise AccountNotFound("游戏 ID 必须是数字")
        projection = SUITE_PROJECTIONS[kind]
        cache_key = (server, game_user_id, projection)
        base = (
            f"{self.settings.mysekai_haruki_base}/api/public/"
            f"{server}/suite/{game_user_id}"
        )
        cached = self._suite_cache.get(cache_key)
        try:
            async with self._client(timeout=60.0) as client:
                version = await self._probe_upload_time(client, base)
                if cached is not None and version is not None and cached[0] == version:
                    return cached[1]
                response = await client.get(base, params={"key": projection})
        except httpx.HTTPError:
            raise HarukiUnavailable("Haruki 暂时不可用，请稍后再试") from None
        if response.status_code == 404:
            raise CaptureStale(
                "这项数据在 Haruki 公开接口不可用（或该账号最近一次抓包没有刷新它）"
            )
        if response.status_code >= 500:
            raise HarukiUnavailable("Haruki 暂时不可用，请稍后再试")
        if response.status_code != 200:
            raise UpstreamError("Haruki 暂时不可用，请稍后再试")
        try:
            data = response.json()
        except ValueError:
            raise UpstreamError("Haruki 返回了无法解析的数据") from None
        if not isinstance(data, dict):
            raise UpstreamError("Haruki 返回了无法解析的数据")
        upload_time = data.get("upload_time")
        self._suite_cache[cache_key] = (
            upload_time if isinstance(upload_time, int) else version,
            data,
        )
        return data

    async def _probe_upload_time(self, client, base: str) -> int | None:
        """The snapshot version, or ``None`` when the probe itself fails."""
        try:
            response = await client.get(base, params={"key": "upload_time"})
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        try:
            data = response.json()
        except ValueError:
            return None
        value = data.get("upload_time") if isinstance(data, dict) else data
        return value if isinstance(value, int) else None

    async def catalog_state_meta(
        self, game_user_id: str, server: str = "cn"
    ) -> dict:
        """Display-ready facts from the last capture: times and 烤森 level."""
        state = await self.repository.get_mysekai_catalog_state(server, game_user_id)
        if state is None:
            return {}
        return {
            # Suite 数据：Haruki 上这份 suite 的更新时间。
            "updated_at": format_capture_time(state.get("suite_uploaded_at_ms")),
            # MySekai 数据：这份烤森抓包到达我们的时间。
            "mysekai_at": format_capture_time(
                state.get("mysekai_uploaded_at_ms")
                or state.get("refreshed_at_ms")
            ),
            "mysekai_rank": state.get("mysekai_rank"),
            # 游戏等级：抓包里没有，来自 Haruki 并缓存。
            "game_rank": state.get("game_rank"),
        }

    async def remember_game_rank(
        self, server: str, game_user_id: str, rank: int | None
    ) -> None:
        """Persist a level we just saw in a Haruki response."""
        if isinstance(rank, int):
            await self.repository.set_mysekai_game_rank(server, game_user_id, rank)

    async def catalog_payload(
        self, game_user_id: str, kind: str, server: str = "cn"
    ) -> dict:
        """The ``user`` rows the renderer needs, from the last uploaded capture.

        Haruki's public suite does not expose records or blueprints, so these two
        cards read what the player already uploaded to us instead.
        """
        state = await self.repository.get_mysekai_catalog_state(server, game_user_id)
        if state is None:
            raise CatalogStateMissing("这个账号还没有上传记录，请先在网页上传一次抓包")
        if kind == "music-record":
            rows = state["music_records"]
            if rows is None:
                raise CatalogStateMissing(
                    "最近一次抓包没有刷新唱片数据，请重新完整上传一次"
                )
            return {"userMysekaiMusicRecords": rows}
        if kind == "convert":
            slots = state.get("convert_slots")
            histories = state.get("convert_histories")
            if slots is None and histories is None:
                raise CatalogStateMissing(
                    "最近一次抓包没有刷新转换机数据，请重新完整上传一次"
                )
            payload: dict = {}
            if slots is not None:
                payload["userMysekaiConvertSlots"] = slots
            if histories is not None:
                payload["userMysekaiConvertItemHistories"] = histories
            # 判「重复」要靠所有权表；没有就整张不传，让卡片按「未知」处理。
            if state.get("music_records") is not None:
                payload["userMysekaiMusicRecords"] = state["music_records"]
            if state.get("blueprints") is not None:
                payload["userMysekaiBlueprints"] = state["blueprints"]
            return payload
        rows = state["blueprints"]
        return {} if rows is None else {"userMysekaiBlueprints": rows}

    async def catalog_hint_payload(
        self, game_user_id: str, server: str = "cn"
    ) -> dict:
        """Stored rows the talk card needs to decide grey-out state.

        Blueprints cover craftable furniture; owned fixtures cover eggs and
        event models, which have no blueprint at all. Unlike
        :meth:`catalog_payload` this never fails: a talk card still renders
        without an upload, it just cannot tell owned from missing.
        """
        state = await self.repository.get_mysekai_catalog_state(server, game_user_id)
        if state is None:
            return {}
        payload: dict = {}
        if state.get("blueprints") is not None:
            payload["userMysekaiBlueprints"] = state["blueprints"]
        if state.get("fixtures") is not None:
            payload["userMysekaiFixtures"] = state["fixtures"]
        return payload

    async def render_catalog_image(
        self,
        endpoint: str,
        data: dict,
        profile: dict | None = None,
        options: dict | None = None,
        *,
        cache: bool = True,
    ) -> bytes:
        """Send one player payload to the render service and return its PNG.

        The body is the cache key: it already includes the player's data
        version, so a repeat command returns the stored PNG without a render.
        Cards whose content depends on the current time (the converter
        countdown) must pass ``cache=False`` -- the body carries no clock, so a
        cached image would freeze the timer.
        """
        payload: dict = {"user": data}
        if profile:
            payload["profile"] = profile
        if options:
            payload.update(options)
        url = f"{self.settings.mysekai_service_url}{endpoint}"
        if not cache:
            return await self._render_catalog_once(url, payload)
        return await self.render_cache.get_or_render(
            RenderCache.key(payload), lambda: self._render_catalog_once(url, payload)
        )

    async def _render_catalog_once(self, url: str, payload: dict) -> bytes:
        """One render-service call; failures are raised, never cached."""
        try:
            async with self._client(timeout=180.0) as client:
                response = await client.post(url, json=payload)
        except httpx.HTTPError:
            raise UpstreamError("出图服务暂时不可用，请稍后再试") from None
        if response.status_code == 200:
            return response.content
        raise _catalog_render_error(response)

    async def convert_done_image(
        self, game_user_id: str, slot_ids: list[int]
    ) -> bytes:
        """提醒用的小卡：只画刚完成的槽位和它们的产出。

        产出取自最近一次抓包的 ``convert_slots``。槽位的 ``obtainResources``
        在批次开始时就已摇好，所以即使玩家之后没有再上传，这里依然准确。
        缓存必须关闭——同一批槽位的数据不会变，但它们是按槽位 id 取的，
        用请求体做 key 会把不同时机的请求混在一起。
        """
        state = await self.repository.get_mysekai_catalog_state("cn", game_user_id)
        slots = (state or {}).get("convert_slots")
        if not slots:
            raise MysekaiError(
                "最近一次抓包里没有转换机数据。",
                code="convert_state_missing",
                status=409,
            )
        # ``None`` here means the capture never carried that table, which the
        # renderer must tell apart from "owns none": an unknown set marks
        # nothing rather than calling every reward a duplicate.
        return await self.render_catalog_image(
            "/render/mysekai/convert-done",
            {
                "userMysekaiConvertSlots": slots,
                "userMysekaiBlueprints": state.get("blueprints"),
                "userMysekaiMusicRecords": state.get("music_records"),
                "slot_ids": slot_ids,
            },
            cache=False,
        )

    async def update_master(self, region: str) -> dict:
        """Ask the render service to install the newest upstream snapshot.

        The render service writes into the host's ``master/`` directory; this
        method only forwards and relays the diff. It never commits anything.
        """
        token = self.settings.mysekai_internal_token
        if not token:
            raise UpstreamError("内部接口未配置，无法更新主数据")
        url = f"{self.settings.mysekai_service_url}/master/update"
        try:
            # 上游归档在国内要十几秒，出图服务自己的超时是 120s；这里必须留出
            # 解析与重载的时间，否则会出现「服务端已经更新完、前端却超时」。
            async with self._client(timeout=300.0) as client:
                response = await client.post(
                    url, json={"region": region}, headers={"X-Mysekai-Admin": token}
                )
        except httpx.HTTPError:
            raise UpstreamError("出图服务暂时不可用，请稍后再试") from None
        if response.status_code != 200:
            message = "主数据更新失败"
            try:
                body = response.json()
                if isinstance(body, dict):
                    message = str(body.get("error") or message)
            except ValueError:
                pass
            raise UpstreamError(message)
        try:
            result = response.json()
        except ValueError:
            raise UpstreamError("出图服务返回了无效数据") from None
        if not isinstance(result, dict):
            return {}
        # The render service reloaded its master tables; every cached PNG was
        # drawn from the previous snapshot, so drop them before the next query.
        self.render_cache.clear()
        self._materials_cache = None
        self._material_card_cache = None
        return result

    async def fetch_birthday_state(self) -> dict | None:
        """Ask the render service whether a birthday window is open right now."""
        url = f"{self.settings.mysekai_service_url}/birthday/state"
        try:
            async with self._client(timeout=10.0) as client:
                response = await client.get(url)
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        try:
            data = response.json()
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    async def fetch_materials(self) -> list[dict]:
        """The render service's ``mysekai_material`` rows, cached per process."""
        if self._materials_cache is not None:
            return self._materials_cache
        url = f"{self.settings.mysekai_service_url}/materials"
        try:
            async with self._client(timeout=30.0) as client:
                response = await client.get(url)
        except httpx.HTTPError:
            raise UpstreamError("材料目录暂时不可用，请稍后再试") from None
        if response.status_code != 200:
            raise UpstreamError("材料目录暂时不可用，请稍后再试")
        try:
            data = response.json()
        except ValueError:
            raise UpstreamError("材料目录返回了无效数据") from None
        rows = data.get("materials") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise UpstreamError("材料目录返回了无效数据")
        materials = [
            {
                "id": int(row["id"]),
                "name": str(row["name"]),
                "rarity": str(row.get("rarity") or ""),
            }
            for row in rows
            if isinstance(row, dict)
            and isinstance(row.get("id"), int)
            and isinstance(row.get("name"), str)
        ]
        self._materials_cache = materials
        return materials

    async def render_material_catalog(self) -> bytes:
        """The icon + official-name sheet, cached until the master changes."""
        if self._material_card_cache is not None:
            return self._material_card_cache
        url = f"{self.settings.mysekai_service_url}/render/materials"
        try:
            async with self._client(timeout=60.0) as client:
                response = await client.get(url)
        except httpx.HTTPError:
            raise UpstreamError("材料对照表暂时不可用，请稍后再试") from None
        if response.status_code != 200:
            raise UpstreamError("材料对照表暂时不可用，请稍后再试")
        self._material_card_cache = response.content
        return response.content

    async def _resolve_highlight_names(        self, names: list[str]
    ) -> tuple[list[dict], list[str], list[str]]:
        """Resolve exact Chinese names; report unknown and duplicate names."""
        materials = await self.fetch_materials()
        by_name: dict[str, list[dict]] = {}
        for row in materials:
            by_name.setdefault(row["name"], []).append(row)
        resolved: list[dict] = []
        seen: set[int] = set()
        unknown: list[str] = []
        ambiguous: list[str] = []
        for raw in names:
            name = raw.strip()
            if not name:
                continue
            matches = by_name.get(name)
            if not matches:
                unknown.append(name)
            elif len(matches) > 1:
                ambiguous.append(name)
            elif matches[0]["id"] not in seen:
                resolved.append(matches[0])
                seen.add(matches[0]["id"])
        return resolved, unknown, ambiguous

    @staticmethod
    def _highlight_name_error(
        unknown: list[str], ambiguous: list[str]
    ) -> MysekaiError:
        parts: list[str] = []
        if unknown:
            parts.append("未找到材料：" + "、".join(unknown))
        if ambiguous:
            parts.append("材料名不唯一：" + "、".join(ambiguous))
        return MysekaiError(
            "；".join(parts), code="highlight_name_invalid", status=422
        )

    async def highlight_status(self, qq: str) -> dict:
        """This QQ's extra highlights, with names from the render service."""
        ids = await self.repository.list_mysekai_highlight_materials(qq)
        materials = await self.fetch_materials()
        by_id = {row["id"]: row for row in materials}
        return {
            "count": len(ids),
            "limit": HIGHLIGHT_LIMIT,
            "materials": [by_id[i] for i in ids if i in by_id],
            "missing_ids": [i for i in ids if i not in by_id],
        }

    async def set_highlights(
        self, qq: str, names: list[str], *, replace: bool = True
    ) -> dict:
        """Add to (default) or replace this QQ's extra highlight list.

        ``replace=False`` merges the named materials into the existing list,
        so "set cotton" after "set battery" keeps both. ``replace=True`` is the
        explicit "replace" command.
        """
        if not names:
            raise MysekaiError("请至少提供一个材料名", code="highlight_empty", status=422)
        resolved, unknown, ambiguous = await self._resolve_highlight_names(names)
        if unknown or ambiguous:
            raise self._highlight_name_error(unknown, ambiguous)
        if not resolved:
            raise MysekaiError("请至少提供一个材料名", code="highlight_empty", status=422)

        existing_ids: list[int] = []
        if replace:
            final_ids = [row["id"] for row in resolved]
            materials = resolved
            added = resolved
        else:
            existing_ids = await self.repository.list_mysekai_highlight_materials(qq)
            existing = set(existing_ids)
            added = [row for row in resolved if row["id"] not in existing]
            final_ids = existing_ids + [row["id"] for row in added]
            by_id = {row["id"]: row for row in await self.fetch_materials()}
            materials = [by_id[i] for i in final_ids if i in by_id]

        if len(final_ids) > HIGHLIGHT_LIMIT:
            suffix = (
                f"；当前已有 {len(existing_ids)} 种" if existing_ids else ""
            )
            raise MysekaiError(
                f"最多只能高亮 {HIGHLIGHT_LIMIT} 种材料{suffix}",
                code="highlight_too_many",
                status=422,
            )

        await self.repository.set_mysekai_highlight_materials(qq, final_ids)
        return {
            "count": len(final_ids),
            "limit": HIGHLIGHT_LIMIT,
            "materials": materials,
            "added": added,
            "mode": "replace" if replace else "add",
        }

    async def remove_highlights(self, qq: str, names: list[str]) -> dict:
        """Remove the named materials from this QQ's extra highlight list."""
        if not names:
            raise MysekaiError("请提供要取消的材料名", code="highlight_empty", status=422)
        resolved, unknown, ambiguous = await self._resolve_highlight_names(names)
        if unknown or ambiguous:
            raise self._highlight_name_error(unknown, ambiguous)
        removed = await self.repository.remove_mysekai_highlight_materials(
            qq, [row["id"] for row in resolved]
        )
        status = await self.highlight_status(qq)
        return {**status, "removed": removed}

    async def clear_highlights(self, qq: str) -> dict:
        """Drop every extra highlight for this QQ."""
        removed = await self.repository.clear_mysekai_highlight_materials(qq)
        return {
            "removed": removed,
            "count": 0,
            "limit": HIGHLIGHT_LIMIT,
            "materials": [],
            "missing_ids": [],
        }

    async def highlight_ids(self, qq: str) -> frozenset[int]:
        """The ids the render service should promote to rare for this QQ."""
        return frozenset(
            await self.repository.list_mysekai_highlight_materials(qq)
        )

    # ------------------------------------------------------------------ data
    async def set_convert_watch(
        self, qq: str, enabled: bool, game_user_id: str | None = None
    ) -> dict:
        """订阅或取消转换机完成提醒。

        ``game_user_id=None`` 作用于该 QQ 的全部已绑定角色，指定时只影响该角色。
        订阅时顺手把每个角色最近一次抓包里的槽位写进提醒表：玩家完全可能是先
        上传抓包、看到槽位在转，才决定订阅。订阅之前就已走完的槽不补推。
        """
        bindings = await self.repository.list_mysekai_bindings(qq)
        if enabled and not bindings:
            raise MysekaiError(
                "请先绑定游戏账号，再订阅转换机提醒。",
                code="not_bound",
                status=409,
            )
        if enabled and game_user_id is not None:
            if not any(row["game_user_id"] == game_user_id for row in bindings):
                raise BindingNotFound("这个游戏 ID 不属于这个 QQ 的绑定")

        targets = await self.repository.set_mysekai_convert_watch(
            qq, enabled, game_user_id
        )
        roles = [
            {"label": f"u{index}", "game_user_id": row["game_user_id"]}
            for index, row in enumerate(bindings, 1)
        ]

        if enabled:
            subscribed = set(targets)
            for row in bindings:
                if subscribed and row["game_user_id"] not in subscribed:
                    continue
                await self.repository.backfill_mysekai_convert_slots(
                    row["server"], row["game_user_id"], quiet_past=True
                )

        return {
            "enabled": enabled,
            "game_user_id": game_user_id,
            "roles": roles,
            "subscribed": [
                f"u{index}"
                for index, row in enumerate(bindings, 1)
                if row["game_user_id"] in set(targets)
            ],
        }

    async def reset_water_watches(self) -> dict:
        """关闭全群浇水监听；轮次历史保留，重新开启后从头计数。"""
        cleared = await self.repository.clear_mysekai_water_watches()
        return {"watches": cleared}

    async def set_water_watch(self, qq: str, enabled: bool) -> dict:
        """Open or close one player's watch, enforcing the birthday window.

        Closing is always allowed. Opening is only allowed from one hour before
        the flower window starts until it ends; the render service is the
        authority on whether such a window exists.

        Repeating the same command is a no-op: re-opening an active watch must
        not reset its counting start, and closing an inactive one changes
        nothing. ``changed`` tells the caller which case it hit.
        """
        active = await self.repository.is_mysekai_water_watch(qq)
        if not enabled:
            if not active:
                return {"enabled": False, "changed": False, "birthday": None}
            await self.repository.set_mysekai_water_watch(qq, False)
            return {"enabled": False, "changed": True, "birthday": None}
        if active:
            return {"enabled": True, "changed": False, "birthday": None}
        state = await self.fetch_birthday_state()
        if state is None:
            raise MysekaiError(
                "暂时无法核对生日窗口，请稍后再试。",
                code="birthday_state_unavailable",
                status=503,
            )
        now = datetime.now(_CN)
        if state.get("active"):
            start_ms = state.get("start_ms")
            end_ms = state.get("end_ms")
            if isinstance(start_ms, int) and isinstance(end_ms, int):
                await self.repository.set_mysekai_birthday_window(
                    character_id=int(state.get("character_id") or 0),
                    character_name=state.get("character_name"),
                    start_ms=start_ms,
                    end_ms=end_ms,
                )
            await self.repository.set_mysekai_water_watch(qq, True)
            return {"enabled": True, "changed": True, "birthday": state}
        next_start_ms = state.get("next_start_ms")
        if isinstance(next_start_ms, int):
            next_start = datetime.fromtimestamp(next_start_ms / 1000, _CN)
            if watch_is_open(now, start=next_start, end=next_start + FLOWER_WINDOW):
                await self.repository.set_mysekai_water_watch(qq, True)
                return {"enabled": True, "changed": True, "birthday": state}
            opens = watch_opens_at(next_start)
            raise MysekaiError(
                "现在还不能开启烤森监听：监听在生日花窗口开始前 1 小时至窗口结束期间可用；"
                f"下一窗口 {opens:%m-%d %H:%M} 起可开启。",
                code="watch_window_closed",
                status=409,
            )
        raise MysekaiError(
            "现在还不能开启烤森监听：当前没有可用的生日窗口。",
            code="watch_window_closed",
            status=409,
        )

    async def expire_water_watches(self) -> dict:
        """Close every watch once the birthday-flower window has ended.

        Two authorities, in order:

        1. the stored window row, once ``end_ms`` has passed;
        2. the local calendar (``mysekai_window.current_flower_window``) when no
           row was ever written — the "open one hour before the window" path
           only writes the watches table, so relying on the row alone left
           those watches open forever.

        A failure to reach the render service no longer aborts the check: the
        local calendar still closes watches, and the outcome is logged either
        way so the next incident is observable.
        """
        state = await self.fetch_birthday_state()
        now = self._now()
        now_ms = int(now.timestamp() * 1000)
        stored = await self.repository.get_mysekai_birthday_window()
        ended: dict | None = None
        cleared = 0
        reason: str | None = None

        if stored is not None and int(stored["end_ms"]) <= now_ms:
            cleared = await self.repository.clear_mysekai_water_watches()
            await self.repository.delete_mysekai_birthday_window()
            ended = stored
            reason = "stored_window_expired"
        elif stored is None:
            watching = await self.repository.count_mysekai_water_watches()
            local = current_flower_window(now)
            if watching and local is None:
                cleared = await self.repository.clear_mysekai_water_watches()
                reason = "local_calendar_closed"
            elif local is not None:
                # Keep the window for the notification even when nothing was
                # cleared; the bot only speaks when ``cleared`` is positive.
                ended = {
                    "character_id": local[0],
                    "character_name": None,
                    "start_ms": int(local[1].timestamp() * 1000),
                    "end_ms": int(local[2].timestamp() * 1000),
                }

        if state is not None and state.get("active"):
            start_ms = state.get("start_ms")
            end_ms = state.get("end_ms")
            if isinstance(start_ms, int) and isinstance(end_ms, int):
                await self.repository.set_mysekai_birthday_window(
                    character_id=int(state.get("character_id") or 0),
                    character_name=state.get("character_name"),
                    start_ms=start_ms,
                    end_ms=end_ms,
                )

        if cleared:
            logger.info(
                "MySekai watch expiry cleared %s watch(es): reason=%s character=%s",
                cleared,
                reason,
                ended.get("character_name") if ended else None,
            )
        elif state is None:
            logger.warning(
                "MySekai watch expiry could not read birthday state; "
                "local calendar fallback applied (cleared=%s reason=%s)",
                cleared,
                reason,
            )

        return {
            "cleared": cleared,
            "reason": reason or ("birthday_state_unavailable" if state is None else None),
            "character_name": ended.get("character_name") if ended else None,
            "start_ms": int(ended["start_ms"]) if ended else None,
            "end_ms": int(ended["end_ms"]) if ended else None,
            "next_character_name": state.get("next_character_name") if state else None,
            "next_start_ms": state.get("next_start_ms") if state else None,
            "active": bool(state.get("active")) if state else False,
        }

    async def reset_convert_watches(self) -> dict:
        """清空所有转换机订阅与槽位记录。

        供超管在提醒逻辑变化后重来一次：老记录按旧口径排队，留着只会误导。
        清空后群友重新订阅，订阅流程会按新逻辑从最近一次抓包回填。
        """
        watches, slots = await self.repository.clear_mysekai_convert_watches()
        return {"watches": watches, "slots": slots}

    async def bind(self, qq: str, game_user_id: str, server: str) -> dict:
        existing = await self.repository.get_mysekai_binding_by_game(
            server, game_user_id
        )
        if existing is not None:
            if existing["qq"] == qq:
                raise BindingConflict(
                    "这个 QQ 已经绑定了这个游戏 ID；要换用其它角色请用「切换烤森角色」",
                    code="already_bound",
                )
            raise BindingConflict(
                "这个游戏 ID 已经绑定了其他 QQ",
                code="game_id_already_bound",
            )
        if len(await self.repository.list_mysekai_bindings(qq)) >= MAX_ROLES_PER_QQ:
            raise BindingConflict(
                f"一个 QQ 最多绑定 {MAX_ROLES_PER_QQ} 个角色，请先解绑一个",
                code="role_limit_reached",
            )
        profile = await self.fetch_haruki_profile(game_user_id, server)
        await self.repository.set_mysekai_data_times(
            server=server,
            game_user_id=profile.game_user_id,
            suite_uploaded_at_ms=profile.suite_uploaded_at_ms,
        )
        avatar = (
            base64.b64encode(profile.avatar_png).decode("ascii")
            if profile.avatar_png
            else None
        )
        conflict = await self.repository.create_mysekai_binding(
            qq=qq,
            server=server,
            game_user_id=profile.game_user_id,
            nickname=profile.nickname,
            deck_id=profile.deck_id,
            leader_card_id=profile.leader_card_id,
            leader_avatar=avatar,
            profile_image_type=profile.profile_image_type,
        )
        if conflict == "qq":
            raise BindingConflict(
                "这个 QQ 已经绑定了游戏 ID，请先解绑后再绑定",
                code="qq_already_bound",
            )
        if conflict == "already_bound":
            raise BindingConflict(
                "这个 QQ 已经绑定了这个游戏 ID；要换用其它角色请用「切换烤森角色」",
                code="already_bound",
            )
        if conflict == "game_user_id":
            raise BindingConflict(
                "这个游戏 ID 已经绑定了其他 QQ",
                code="game_id_already_bound",
            )
        binding = await self.repository.get_mysekai_binding_by_game(
            server, game_user_id
        )
        if binding is None:  # pragma: no cover - insert guarantees it
            raise UpstreamError("绑定保存失败")
        return binding

    async def query_binding(self, qq: str) -> dict | None:
        return await self.repository.get_mysekai_binding(qq)

    async def list_bindings(self, qq: str) -> list[dict]:
        return await self.repository.list_mysekai_bindings(qq)

    async def switch_role(self, qq: str, game_user_id: str) -> dict:
        """Make one of the QQ's bound roles the default for every query."""
        binding = await self.repository.get_mysekai_binding_by_game("cn", game_user_id)
        if binding is None or binding["qq"] != qq:
            raise BindingNotFound("这个 QQ 没有绑定该游戏 ID，请先绑定")
        await self.repository.set_current_mysekai_role(
            qq, binding["server"], game_user_id
        )
        return binding

    async def unbind(self, qq: str, game_user_id: str | None = None) -> dict:
        binding = await self.repository.delete_mysekai_binding(qq, game_user_id)
        if binding is None:
            raise BindingNotFound("这个 QQ 还没有绑定游戏 ID")
        return binding

    # ------------------------------------------------------------------ render
    async def _render(
        self,
        body: bytes,
        profile_payload: dict,
        visit_baseline: dict | None = None,
        highlight_ids: frozenset[int] = frozenset(),
        watch_on: bool = False,
    ) -> RenderResult:
        header = _b64url_encode(
            json.dumps(
                profile_payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        headers = {
            "Content-Type": "application/octet-stream",
            "X-Mysekai-Profile": header,
            "X-Mysekai-Watch": "1" if watch_on else "0",
        }
        if visit_baseline:
            headers["X-Mysekai-Visit-Baseline"] = _b64url_encode(
                json.dumps(visit_baseline, separators=(",", ":")).encode("utf-8")
            )
        if highlight_ids:
            headers["X-Mysekai-Highlight"] = _b64url_encode(
                json.dumps(
                    sorted(highlight_ids), separators=(",", ":")
                ).encode("utf-8")
            )
        url = f"{self.settings.mysekai_service_url}/submit"
        try:
            async with self._client(timeout=180.0) as client:
                response = await client.post(
                    url,
                    content=body,
                    headers=headers,
                )
        except httpx.HTTPError:
            raise UpstreamError("出图服务暂时不可用，请稍后再试") from None
        if response.status_code in (400, 422):
            message = _error_message(response)
            if message == "no_harvest_maps":
                raise MysekaiError(
                    "本次响应未携带采集图，尚未检查本轮生日花；请上传含地图的响应",
                    code="capture_missing_maps", status=422,
                )
            if message == "watch_required":
                raise MysekaiError(
                    "请先在群里发送「开启烤森监听」，再上传浇水回执",
                    code="birthday_watch_required", status=422,
                )
            raise MysekaiError(
                message or "这份响应无法生成采集图",
                code="invalid_response",
                status=422,
            )
        if response.status_code != 200:
            logger.warning(
                "mysekai render failed status=%s body=%s",
                response.status_code,
                response.text[:300],
            )
            raise UpstreamError("出图服务暂时不可用，请稍后再试")
        try:
            data = response.json()
            if str(data.get("kind") or "snapshot") == "birthday_delivery":
                return RenderResult(
                    sites=[],
                    points=0,
                    drops=0,
                    map_png_base64="",
                    card_png_base64="",
                    visit_counts={},
                    birthday=data.get("birthday"),
                    catalog_state=None,
                    kind="birthday_delivery",
                    rare_png_base64=data.get("rare_png_base64"),
                    birthday_png_base64=data.get("birthday_png_base64"),
                    message=data.get("message"),
                )
            return RenderResult(
                sites=list(data.get("sites") or []),
                points=int(data.get("points") or 0),
                drops=int(data.get("drops") or 0),
                map_png_base64=str(data["map_png_base64"]),
                card_png_base64=str(data["card_png_base64"]),
                visit_counts={
                    str(key): int(value)
                    for key, value in (data.get("visit_counts") or {}).items()
                },
                birthday=data.get("birthday"),
                catalog_state=(
                    data["catalog_state"]
                    if isinstance(data.get("catalog_state"), dict)
                    else None
                ),
                birthday_png_base64=data.get("birthday_png_base64"),
            )
        except (ValueError, KeyError, TypeError):
            raise UpstreamError("出图服务返回了无法解析的数据") from None

    async def _binding_for_role(
        self, qq: str, game_user_id: str | None
    ) -> dict:
        """The binding for one role, defaulting to the QQ's current role.

        Every role-scoped read goes through here so "which role" is answered in
        exactly one place instead of each endpoint re-deriving it.
        """
        binding = await self.repository.get_mysekai_binding(qq)
        if binding is None:
            raise BindingNotFound("这个 QQ 还没有绑定游戏 ID，请先绑定")
        if game_user_id and game_user_id != binding["game_user_id"]:
            roles = await self.repository.list_mysekai_bindings(qq)
            binding = next(
                (row for row in roles if row["game_user_id"] == game_user_id), None
            )
            if binding is None:
                raise BindingNotFound("这个游戏 ID 不属于这个 QQ 的绑定")
        return binding

    async def help_image(self, sections: list[dict]) -> bytes:
        """Render the command help sheet from the list the Bot maintains."""
        url = f"{self.settings.mysekai_service_url}/render/mysekai/help"
        try:
            async with self._client(timeout=60.0) as client:
                response = await client.post(url, json={"sections": sections})
        except httpx.HTTPError:
            raise UpstreamError("帮助图暂时不可用，请稍后再试") from None
        if response.status_code != 200:
            raise UpstreamError("帮助图暂时不可用，请稍后再试")
        return response.content

    async def latest_capture(
        self, qq: str, game_user_id: str | None = None
    ) -> dict:
        """The stored map and card for one role (default: the current one)."""
        binding = await self._binding_for_role(qq, game_user_id)
        row = await self.repository.get_mysekai_latest_capture(
            qq, binding["game_user_id"]
        )
        if row is None:
            raise MysekaiError(
                "还没有上传过烤森抓包", code="no_capture", status=404
            )
        return {
            **row,
            "game_user_id": binding["game_user_id"],
            "role_label": await self._role_label(qq, binding["game_user_id"]),
        }

    async def visit_chart(self, qq: str, game_user_id: str | None = None) -> dict:
        """Render the character visit-count ranking from the last capture.

        ``game_user_id`` selects one of the QQ's roles; omitting it uses the
        current one, same as every other query command.
        """
        binding = await self._binding_for_role(qq, game_user_id)
        row = await self.repository.get_mysekai_visit_totals(
            binding["server"], binding["game_user_id"]
        )
        if row is None or not row["groups"]:
            raise MysekaiError(
                "还没有上传过烤森抓包", code="no_capture", status=404
            )
        payload = {
            "role_label": await self._role_label(qq, binding["game_user_id"]),
            "captured_at": format_capture_time(row["captured_at_ms"]),
            "groups": row["groups"],
        }
        url = f"{self.settings.mysekai_service_url}/render/mysekai/visits"
        try:
            async with self._client(timeout=60.0) as client:
                response = await client.post(url, json=payload)
        except httpx.HTTPError:
            raise UpstreamError("出图服务暂时不可用，请稍后再试") from None
        if response.status_code == 404:
            raise MysekaiError("还没有可用的来访记录", code="no_capture", status=404)
        if response.status_code != 200:
            logger.warning(
                "mysekai visit chart failed status=%s body=%s",
                response.status_code,
                response.text[:300],
            )
            raise UpstreamError("出图服务暂时不可用，请稍后再试")
        return {
            "png": response.content,
            "role_label": payload["role_label"],
            "captured_at_ms": row["captured_at_ms"],
        }

    async def birthday_progress_image(
        self, qq: str, game_user_id: str | None = None
    ) -> bytes:
        """The stored birthday progress, rendered by the pipeline.

        ``game_user_id`` selects one of the QQ's roles; omitting it uses the
        current one, same as every other query command.
        """
        binding = await self._binding_for_role(qq, game_user_id)
        state = await self.repository.get_mysekai_birthday_state(
            binding["server"], binding["game_user_id"]
        )
        if state is None:
            raise MysekaiError(
                "还没有你的生日数据，请先上传一次带地图的响应或浇水回执",
                code="birthday_state_missing",
                status=404,
            )
        url = f"{self.settings.mysekai_service_url}/render/birthday"
        payload = {
            "region": binding["server"],
            "party_id": state["party_id"],
            "obtained": state["obtained"],
            "dropped": state["dropped"],
            "total_point": state["total_point"],
            "captured_at_ms": state["captured_at_ms"],
        }
        try:
            async with self._client(timeout=60.0) as client:
                response = await client.post(url, json=payload)
        except httpx.HTTPError:
            raise UpstreamError("生日进度服务暂时不可用，请稍后再试") from None
        if response.status_code == 404:
            raise MysekaiError(
                "当前没有进行中的生日活动",
                code="no_active_birthday",
                status=404,
            )
        if response.status_code != 200:
            raise UpstreamError("生日进度服务暂时不可用，请稍后再试")
        return response.content

    async def ingest(
        self,
        *,
        game_user_id: str,
        server: str,
        body: bytes,
        event_kind: str = "snapshot",
        birthday_party_id: int | None = None,
    ) -> dict:
        """Render a response body pushed by the mobile capture path.

        The body itself carries no game id, so the caller-supplied id is the
        only link back to a bound QQ; an id with no binding is rejected before
        any decryption or rendering happens.
        """
        binding = await self.repository.get_mysekai_binding_by_game(
            server, game_user_id
        )
        if binding is None:
            raise BindingNotFound("这个游戏 ID 还没有绑定 QQ，请先在群里绑定")
        return await self.submit(
            qq=binding["qq"],
            game_user_id=game_user_id,
            server=server,
            file_base64=base64.b64encode(body).decode("ascii"),
            event_kind=event_kind, birthday_party_id=birthday_party_id,
        )

    async def ingest_convert(
        self, *, game_user_id: str, server: str, body: bytes
    ) -> dict:
        """Refresh a player's converter slots from a converter-action response.

        These responses are partial documents, so this path never renders,
        never pushes and never records an upload: it only replaces
        ``convert_slots`` when the response actually carried the table.
        """
        binding = await self.repository.get_mysekai_binding_by_game(
            server, game_user_id
        )
        if binding is None:
            raise BindingNotFound("这个游戏 ID 还没有绑定 QQ，请先在群里绑定")
        url = f"{self.settings.mysekai_service_url}/convert-state"
        try:
            async with self._client(timeout=60.0) as client:
                response = await client.post(
                    url,
                    content=body,
                    headers={"Content-Type": "application/octet-stream"},
                )
        except httpx.HTTPError:
            raise UpstreamError("出图服务暂时不可用，请稍后再试") from None
        if response.status_code != 200:
            raise UpstreamError("转换机数据暂时无法解析，请稍后再试")
        try:
            payload = response.json()
        except ValueError:
            raise UpstreamError("转换机数据返回了无效内容") from None
        if not isinstance(payload, dict) or not payload.get("present"):
            logger.info(
                "我的世界转换机上报 game_id=%s present=false", game_user_id
            )
            return {"ok": True, "updated": False}
        slots = payload.get("slots") or []
        state = await self.repository.get_mysekai_catalog_state(
            server, game_user_id
        ) or {}
        await self.repository.set_mysekai_catalog_state(
            server=server,
            game_user_id=game_user_id,
            music_records=state.get("music_records"),
            blueprints=state.get("blueprints"),
            fixtures=state.get("fixtures"),
            convert_slots=slots,
            convert_histories=state.get("convert_histories"),
            mysekai_rank=state.get("mysekai_rank"),
            refreshed_at_ms=state.get("refreshed_at_ms"),
        )
        if await self.repository.is_mysekai_convert_watch(
            binding["qq"], game_user_id
        ):
            await self.repository.record_mysekai_convert_slots(
                game_user_id, slots
            )
        logger.info(
            "我的世界转换机上报 game_id=%s present=true slots=%d",
            game_user_id,
            len(slots),
        )
        return {"ok": True, "updated": True, "slots": len(slots)}

    async def _role_label(self, qq: str, game_user_id: str) -> str | None:
        """``u1``/``u2``… for this role, matching what ``查询MySekai绑定`` shows.

        Returns ``None`` when the role is not bound, so the caller can omit the
        label instead of printing a wrong number.
        """
        bindings = await self.repository.list_mysekai_bindings(qq)
        for index, row in enumerate(bindings, 1):
            if row["game_user_id"] == game_user_id:
                return f"u{index}"
        return None

    async def _push(
        self,
        *,
        upload_id: str,
        qq: str,
        game_user_id: str,
        nickname: str | None,
        map_png_base64: str,
        card_png_base64: str,
        birthday: dict | None = None,
    ) -> tuple[str, str]:
        url = self.settings.mysekai_push_url
        if not url:
            return "unconfigured", "推送未配置"
        payload = {
            "upload_id": upload_id,
            "qq": qq,
            "game_user_id": game_user_id,
            "nickname": nickname,
            "role_label": await self._role_label(qq, game_user_id),
            "map_png_base64": map_png_base64,
            "card_png_base64": card_png_base64,
            "birthday": birthday,
        }
        headers = {"Content-Type": "application/json"}
        if self.settings.mysekai_push_token:
            headers["Authorization"] = f"Bearer {self.settings.mysekai_push_token}"
        try:
            async with self._client(timeout=60.0) as client:
                response = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError:
            return "failed", "推送失败（网络错误）"
        if 200 <= response.status_code < 300:
            return "sent", "已推送到群里"
        return "failed", f"推送失败（HTTP {response.status_code}）"

    async def _render_push(self, body: bytes) -> bytes | None:
        """One composed image: the rare map plus the birthday progress strip."""
        url = f"{self.settings.mysekai_service_url}/render/push"
        try:
            async with self._client(timeout=120.0) as client:
                response = await client.post(
                    url,
                    content=body,
                    headers={"Content-Type": "application/octet-stream"},
                )
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        return response.content

    async def _push_rare(
        self,
        *,
        upload_id: str,
        qq: str,
        game_user_id: str,
        nickname: str | None,
        birthday: dict,
        rare_png: bytes | None,
    ) -> tuple[str, str]:
        """Push a watering-watch hit: one text line plus the filtered map."""
        url = self.settings.mysekai_push_url
        if not url:
            return "unconfigured", "推送未配置"
        payload = {
            "upload_id": upload_id,
            "qq": qq,
            "game_user_id": game_user_id,
            "nickname": nickname,
            "role_label": await self._role_label(qq, game_user_id),
            "birthday": birthday,
            "rare_png_base64": base64.b64encode(rare_png).decode("ascii") if rare_png is not None else None,
            "empty_result": rare_png is None,
        }
        headers = {"Content-Type": "application/json"}
        if self.settings.mysekai_push_token:
            headers["Authorization"] = f"Bearer {self.settings.mysekai_push_token}"
        try:
            async with self._client(timeout=60.0) as client:
                response = await client.post(
                    f"{url}/rare", json=payload, headers=headers
                )
        except httpx.HTTPError:
            return "failed", "稀有掉落推送失败（网络错误）"
        if 200 <= response.status_code < 300:
            return "sent", "已推送稀有掉落到群里"
        return "failed", f"稀有掉落推送失败（HTTP {response.status_code}）"

    async def submit(
        self,
        *,
        qq: str,
        game_user_id: str,
        server: str,
        file_base64: str,
        event_kind: str = "manual",
        birthday_party_id: int | None = None,
    ) -> dict:
        bindings = await self.repository.list_mysekai_bindings(qq)
        if not bindings:
            raise BindingNotFound("这个 QQ 还没有绑定游戏 ID，请先在群里绑定")
        binding = next(
            (row for row in bindings if row["game_user_id"] == game_user_id), None
        )
        if binding is None:
            raise BindingMismatch(
                "填写的游戏 ID 不属于这个 QQ 的绑定，已拒绝出图"
            )
        if binding["server"] != server:
            raise BindingMismatch("绑定的服务器与本次选择不一致")

        watch_on = await self.repository.is_mysekai_water_watch(qq)

        try:
            refreshed = await self.fetch_haruki_profile(game_user_id, server)
        except HarukiUnavailable:
            pass
        else:
            avatar = (
                base64.b64encode(refreshed.avatar_png).decode("ascii")
                if refreshed.avatar_png
                else (
                    binding.get("leader_avatar")
                    if refreshed.leader_card_id == binding.get("leader_card_id")
                    else None
                )
            )
            updated = await self.repository.update_mysekai_binding_profile(
                qq=qq,
                # 必须锁定本次上传的角色：不传就会更新「当前角色」，
                # 返回行还会把下面的 binding 覆盖成另一个角色。
                game_user_id=game_user_id,
                nickname=refreshed.nickname,
                deck_id=refreshed.deck_id,
                leader_card_id=refreshed.leader_card_id,
                leader_avatar=avatar,
                profile_image_type=refreshed.profile_image_type,
            )
            if updated is None:
                raise BindingNotFound("这个 QQ 的绑定已经解除，请重新绑定后再上传")
            if updated.get("game_user_id") != game_user_id:
                raise UpstreamError("绑定资料更新返回了另一个角色，已拒绝出图")
            binding = updated

        try:
            body = base64.b64decode(file_base64, validate=True)
        except (ValueError, TypeError):
            raise MysekaiError("文件内容不是有效的数据") from None
        if not body:
            raise MysekaiError("响应体为空")
        if len(body) > MAX_BODY_BYTES:
            raise MysekaiError("响应体超过 4 MB")
        avatar = binding.get("leader_avatar")
        meta = await self.catalog_state_meta(server, game_user_id)
        profile_payload = {
            "nickname": binding.get("nickname"),
            "user_id": binding["game_user_id"],
            "region": "CN",
            "hide_uid": True,
            # 缓存下来的游戏等级；抓包本身只有烤森等级。
            "game_rank": meta.get("game_rank"),
            "source": "Haruki",
            "mode": "latest",
            "avatar_base64": avatar,
            # 这份抓包到达我们的时间；资料卡的「MySekai 数据」行。
            "mysekai_at": format_capture_time(int(time.time() * 1000)),
        }
        day = _game_day_key()
        visit_baseline = await self.repository.get_mysekai_visit_baseline(
            qq, binding["game_user_id"], day
        )
        highlight_ids = await self.highlight_ids(qq)
        result = await self._render(
            body, profile_payload, visit_baseline, highlight_ids, watch_on=watch_on
        )
        if result.kind == "birthday_delivery":
            event_kind = "birthday_delivery"
            birthday_party_id = (result.birthday or {}).get("party_id")
        if result.visit_counts:
            # 计数是累计值、名单是当天的：合并保留历史最高值，来过一次就一直留在榜上。
            captured_ms = (
                (result.catalog_state or {}).get("refreshed_at_ms")
                or int(time.time() * 1000)
            )
            previous = await self.repository.get_mysekai_visit_totals(
                server, binding["game_user_id"]
            )
            merged = dict((previous or {}).get("groups") or {})
            for key, value in result.visit_counts.items():
                merged[key] = max(int(value), int(merged.get(key, 0)))
            await self.repository.set_mysekai_visit_totals(
                server,
                binding["game_user_id"],
                merged,
                int(captured_ms),
            )
        if result.catalog_state is not None:
            await self.repository.set_mysekai_catalog_state(
                server=server,
                game_user_id=binding["game_user_id"],
                music_records=result.catalog_state.get("music_records"),
                blueprints=result.catalog_state.get("blueprints"),
                fixtures=result.catalog_state.get("fixtures"),
                convert_slots=result.catalog_state.get("convert_slots"),
                convert_histories=result.catalog_state.get("convert_histories"),
                mysekai_rank=result.catalog_state.get("mysekai_rank"),
                refreshed_at_ms=result.catalog_state.get("refreshed_at_ms"),
            )
        if result.catalog_state is not None and await self.repository.is_mysekai_convert_watch(
            qq, binding["game_user_id"]
        ):
            slots = result.catalog_state.get("convert_slots")
            if slots:
                await self.repository.record_mysekai_convert_slots(
                    binding["game_user_id"], slots
                )
        if visit_baseline is None and result.visit_counts:
            await self.repository.set_mysekai_visit_baseline(
                qq, binding["game_user_id"], day, result.visit_counts
            )
        upload_id = str(uuid.uuid4())
        if event_kind == "birthday_delivery" and (
            not result.birthday
            or result.birthday.get("party_id") != birthday_party_id
        ):
            # URL 里的活动 ID 只是模块带的元数据；响应本身可信，口径不一致时
            # 记日志并按响应内容继续，绝不能因此吞掉一次真实出货。
            birthday = result.birthday or {}
            logger.warning(
                "mysekai birthday delivery mismatch upload_id=%s expected_party=%s"
                " actual_party=%s has_birthday=%s round_source=%s flowers=%s"
                " round_known=%s",
                upload_id,
                birthday_party_id,
                birthday.get("party_id"),
                bool(result.birthday),
                birthday.get("round_source"),
                birthday.get("flower_points"),
                birthday.get("round_known"),
            )
        await self.repository.set_mysekai_data_times(
            server=server,
            game_user_id=binding["game_user_id"],
            mysekai_uploaded_at_ms=int(time.time() * 1000),
        )
        birthday = result.birthday or {}
        if birthday.get("party_id") is not None and birthday.get("obtained") is not None:
            try:
                await self.repository.set_mysekai_birthday_state(
                    server=server,
                    game_user_id=binding["game_user_id"],
                    party_id=int(birthday["party_id"]),
                    character_id=int(birthday.get("character_id") or 0),
                    obtained=int(birthday.get("obtained") or 0),
                    dropped=int(birthday.get("dropped") or 0),
                    total_point=int(birthday.get("total_point") or 0),
                    captured_at_ms=(
                        (result.catalog_state or {}).get("refreshed_at_ms")
                        or int(time.time() * 1000)
                    ),
                )
            except Exception:  # 进度缓存坏了不能拖垮上传
                logger.warning("生日进度落库失败", exc_info=True)
        await self.repository.record_mysekai_upload(
            upload_id=upload_id,
            qq=qq,
            server=server,
            game_user_id=binding["game_user_id"],
            body_sha256=hashlib.sha256(body).hexdigest(),
            sites=len(result.sites),
            points=result.points,
            drops=result.drops,
            push_status="pending",
        )
        if result.kind != "birthday_delivery":
            try:
                await self.repository.save_mysekai_latest_capture(
                    qq=qq,
                    game_user_id=binding["game_user_id"],
                    upload_id=upload_id,
                    captured_at_ms=(
                        (result.catalog_state or {}).get("refreshed_at_ms")
                    ),
                    uploaded_at_ms=int(time.time() * 1000),
                    map_png=base64.b64decode(result.map_png_base64),
                    card_png=base64.b64decode(result.card_png_base64),
                )
            except Exception:  # 只是指令用的缓存，坏了不能拖垮上传
                logger.warning("我的世界最近抓包写入失败", exc_info=True)
        moment = datetime.now(_CN)
        window = window_key(moment, birthdays=birthday_dates(moment.year))
        claimed = event_kind != "birthday_delivery"
        if claimed and self.settings.mysekai_push_dedupe and not is_exempt(
            watch_on, result.birthday
        ):
            try:
                claimed = await self.repository.claim_mysekai_push_window(
                    binding["game_user_id"], window, upload_id
                )
            except Exception:  # 去重坏了就失败即放行，宁可多推一条
                logger.warning("我的世界推送窗口占位失败，改为照常推送", exc_info=True)
                claimed = True
        if claimed:
            push_status, push_message = await self._push(
                upload_id=upload_id,
                qq=qq,
                game_user_id=binding["game_user_id"],
                nickname=binding.get("nickname"),
                map_png_base64=result.map_png_base64,
                card_png_base64=result.card_png_base64,
                # 生日结果只由独立轮次通知发送，普通地图不附加播报。
                birthday=None,
            )
            if push_status != "sent":
                # 推失败不能占着窗口，否则本时段后续上传会被静音。
                try:
                    await self.repository.release_mysekai_push_window(
                        binding["game_user_id"], window, upload_id
                    )
                except Exception:
                    logger.warning("我的世界推送窗口释放失败", exc_info=True)
        else:
            push_status = "skipped"
            push_message = (
                "生日刷新仅发送监听结果" if event_kind == "birthday_delivery"
                else "本时段已推送过，本次仅更新数据"
            )
        await self.repository.update_mysekai_upload_push_status(
            upload_id, push_status
        )
        watch_result = {"status": "skipped", "message": "本次未触发浇水监听"}
        if watch_on and result.birthday and event_kind in {"birthday_delivery", "manual"}:
            watch_result = {"status": "unknown", "message": "缺少当前活动进度，无法识别轮次"}
            round_key = str(result.birthday.get("round_key") or "")
            if round_key:
                # 每轮都入库便于查询；只有命中稀有才播报，避免连续浇水刷屏。
                has_targets = bool(result.birthday.get("targets"))
                push_png = await self._render_push(body) if has_targets else None
                claimed = False
                if not has_targets:
                    claimed = await self.repository.claim_mysekai_water_round(
                        binding["game_user_id"], round_key
                    )
                    watch_result = {
                        "status": "recorded" if claimed else "duplicate",
                        "message": "本轮已记录，未发现稀有材料" if claimed
                        else "这一轮已经记录过",
                    }
                elif not push_png:
                    watch_result = {
                        "status": "failed",
                        "message": "检测到稀有材料，但点位图生成失败，请重试",
                    }
                else:
                    claimed = await self.repository.claim_mysekai_water_round(
                        binding["game_user_id"], round_key
                    )
                    if not claimed:
                        watch_result = {"status": "duplicate", "message": "这一轮已经播报过"}
                if has_targets and push_png and claimed:
                    await self.repository.mark_mysekai_water_round_matched(
                        binding["game_user_id"], round_key
                    )
                    notification_id = str(uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"mysekai:{server}:{binding['game_user_id']}:{round_key}",
                    ))
                    rare_status, rare_message = await self._push_rare(
                        upload_id=notification_id,
                        qq=qq,
                        game_user_id=binding["game_user_id"],
                        nickname=binding.get("nickname"),
                        birthday={
                            **result.birthday,
                            "observation_kind": "delivery" if event_kind == "birthday_delivery" else "snapshot",
                        },
                        rare_png=push_png,
                    )
                    watch_result = {"status": rare_status, "message": rare_message}
                    if rare_status != "sent":
                        await self.repository.release_mysekai_water_round(
                            binding["game_user_id"], round_key
                        )
                        logger.warning(
                            "mysekai rare push failed upload_id=%s reason=%s",
                            upload_id, rare_message,
                        )
        if result.kind == "birthday_delivery":
            return {
                "kind": "birthday_delivery",
                "upload_id": upload_id,
                "profile": {
                    "qq": qq,
                    "game_user_id": binding["game_user_id"],
                    "nickname": binding.get("nickname"),
                    "avatar_data_url": (
                        f"data:image/png;base64,{avatar}" if avatar else None
                    ),
                },
                "message": result.message,
                "rare": (
                    f"data:image/png;base64,{result.rare_png_base64}"
                    if result.rare_png_base64
                    else None
                ),
                "birthday": (
                    f"data:image/png;base64,{result.birthday_png_base64}"
                    if result.birthday_png_base64
                    else None
                ),
                "push": {"status": push_status, "message": push_message},
                "watch": watch_result,
            }
        return {
            "upload_id": upload_id,
            "profile": {
                "qq": qq,
                "game_user_id": binding["game_user_id"],
                "nickname": binding.get("nickname"),
                "avatar_data_url": (
                    f"data:image/png;base64,{avatar}" if avatar else None
                ),
            },
            "sites": result.sites,
            "points": result.points,
            "drops": result.drops,
            "map": f"data:image/png;base64,{result.map_png_base64}",
            "card": f"data:image/png;base64,{result.card_png_base64}",
            "push": {"status": push_status, "message": push_message},
            "watch": watch_result,
        }


def _catalog_render_error(response) -> MysekaiError:
    """Map the render service's typed error body back to a domain error."""
    code = ""
    message = ""
    try:
        body = response.json()
        if isinstance(body, dict):
            code = str(body.get("error") or "")
            message = str(body.get("message") or "")
    except ValueError:
        body = None
    if response.status_code == 409 or code == "capture_stale":
        return CaptureStale(message or "该账号最近一次抓包没有刷新这项数据，请重新上传后再试")
    typed = {
        "character_unknown": CharacterUnknown,
        "virtual_singer_unit_required": VirtualSingerUnitRequired,
        "unit_unknown": UnitUnknown,
    }.get(code)
    if response.status_code == 422 and typed is not None:
        return typed(message or "参数不正确")
    return UpstreamError(message or "出图服务暂时不可用，请稍后再试")


#: Unixtime in seconds stays far below 1e11 until year 5138, while today's
#: millisecond timestamps are ~1e12. The gap makes the unit unambiguous.
_MILLISECONDS_FLOOR = 100_000_000_000


def _capture_ms(value: object) -> int | None:
    """A game/Haruki timestamp as milliseconds, accepting seconds too."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value > 0:
        raw = int(value)
        return raw if raw >= _MILLISECONDS_FLOOR else raw * 1000
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.isdigit():
            return _capture_ms(int(text))
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        return int(moment.timestamp() * 1000)
    return None


def format_capture_time(value: str | int | float | None) -> str | None:
    """A capture timestamp as ``YYYY-MM-DD HH:MM`` in China time.

    Accepts Haruki's ``upload_time`` (unix seconds), the pipeline's
    ``refreshed_at_ms`` (unix milliseconds), and the stored ISO string.
    """
    if isinstance(value, (int, float)) and value > 0:
        seconds = value / 1000 if value >= _MILLISECONDS_FLOOR else value
        moment = datetime.fromtimestamp(seconds, _CN)
        return moment.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, str) and value:
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return moment.astimezone(_CN).strftime("%Y-%m-%d %H:%M")
    return None
