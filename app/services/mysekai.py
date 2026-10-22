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


COOKIE_NAME = "paro_mysekai"
SESSION_TTL_SECONDS = 12 * 60 * 60
MAX_BODY_BYTES = 4 * 1024 * 1024
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


def _today_key() -> str:
    """The game day under UTC+8, used as the baseline partition."""
    return datetime.now(_CN).strftime("%Y-%m-%d")


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

    # ------------------------------------------------------------------ session
    def enabled(self) -> bool:
        return bool(self.settings.mysekai_password)

    def verify_password(self, password: str) -> None:
        if not self.enabled():
            raise MysekaiDisabled("入口还没有开启（未配置访问密码）")
        expected = self.settings.mysekai_password.encode("utf-8")
        actual = password.encode("utf-8")
        if not hmac.compare_digest(actual, expected):
            raise InvalidPassword("密码不正确")

    def issue_token(self) -> str:
        payload = json.dumps(
            {"exp": int(time.time()) + SESSION_TTL_SECONDS},
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
        returned_id = str(
            gamedata.get("userId")
            or user_profile.get("userId")
            or ""
        )
        if returned_id != game_user_id:
            raise UpstreamError("Haruki 返回的账号与请求不一致")
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
            "updated_at": format_capture_time(state.get("updated_at")),
            "mysekai_at": format_capture_time(state.get("refreshed_at_ms")),
            "mysekai_rank": state.get("mysekai_rank"),
        }

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
    ) -> bytes:
        """Send one player payload to the render service and return its PNG.

        The body is the cache key: it already includes the player's data
        version, so a repeat command returns the stored PNG without a render.
        """
        payload: dict = {"user": data}
        if profile:
            payload["profile"] = profile
        if options:
            payload.update(options)
        url = f"{self.settings.mysekai_service_url}{endpoint}"
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
            async with self._client(timeout=180.0) as client:
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

    # ------------------------------------------------------------------ data
    async def bind(self, qq: str, game_user_id: str, server: str) -> dict:
        if await self.repository.get_mysekai_binding(qq) is not None:
            raise BindingConflict(
                "这个 QQ 已经绑定了游戏 ID，请先解绑后再绑定",
                code="qq_already_bound",
            )
        if (
            await self.repository.get_mysekai_binding_by_game(server, game_user_id)
            is not None
        ):
            raise BindingConflict(
                "这个游戏 ID 已经绑定了其他 QQ",
                code="game_id_already_bound",
            )
        profile = await self.fetch_haruki_profile(game_user_id, server)
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
        if conflict == "game_user_id":
            raise BindingConflict(
                "这个游戏 ID 已经绑定了其他 QQ",
                code="game_id_already_bound",
            )
        binding = await self.repository.get_mysekai_binding(qq)
        if binding is None:  # pragma: no cover - insert guarantees it
            raise UpstreamError("绑定保存失败")
        return binding

    async def query_binding(self, qq: str) -> dict | None:
        return await self.repository.get_mysekai_binding(qq)

    async def unbind(self, qq: str) -> dict:
        binding = await self.repository.delete_mysekai_binding(qq)
        if binding is None:
            raise BindingNotFound("这个 QQ 还没有绑定游戏 ID")
        return binding

    # ------------------------------------------------------------------ render
    async def _render(
        self, body: bytes, profile_payload: dict, visit_baseline: dict | None = None
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
        }
        if visit_baseline:
            headers["X-Mysekai-Visit-Baseline"] = _b64url_encode(
                json.dumps(visit_baseline, separators=(",", ":")).encode("utf-8")
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
            raise MysekaiError(
                message or "这份响应无法生成采集图",
                code="invalid_response",
                status=422,
            )
        if response.status_code != 200:
            raise UpstreamError("出图服务暂时不可用，请稍后再试")
        try:
            data = response.json()
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
            )
        except (ValueError, KeyError, TypeError):
            raise UpstreamError("出图服务返回了无法解析的数据") from None

    async def ingest(
        self,
        *,
        game_user_id: str,
        server: str,
        body: bytes,
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
        )

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

    async def _render_rare(self, body: bytes) -> bytes | None:
        """Ask the render service for a map that only carries the watched drops."""
        url = f"{self.settings.mysekai_service_url}/render/rare"
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
        rare_png: bytes,
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
            "birthday": birthday,
            "rare_png_base64": base64.b64encode(rare_png).decode("ascii"),
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
    ) -> dict:
        binding = await self.repository.get_mysekai_binding(qq)
        if binding is None:
            raise BindingNotFound("这个 QQ 还没有绑定游戏 ID，请先在群里绑定")
        if binding["game_user_id"] != game_user_id:
            raise BindingMismatch(
                "填写的游戏 ID 与这个 QQ 的绑定不一致，已拒绝出图"
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
                nickname=refreshed.nickname,
                deck_id=refreshed.deck_id,
                leader_card_id=refreshed.leader_card_id,
                leader_avatar=avatar,
                profile_image_type=refreshed.profile_image_type,
            )
            if updated is None:
                raise BindingNotFound("这个 QQ 的绑定已经解除，请重新绑定后再上传")
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
        profile_payload = {
            "nickname": binding.get("nickname"),
            "user_id": binding["game_user_id"],
            "region": "CN",
            "hide_uid": True,
            "source": "Haruki",
            "mode": "latest",
            "avatar_base64": avatar,
        }
        day = _today_key()
        visit_baseline = await self.repository.get_mysekai_visit_baseline(qq, day)
        result = await self._render(body, profile_payload, visit_baseline)
        if result.catalog_state is not None:
            await self.repository.set_mysekai_catalog_state(
                server=server,
                game_user_id=binding["game_user_id"],
                music_records=result.catalog_state.get("music_records"),
                blueprints=result.catalog_state.get("blueprints"),
                fixtures=result.catalog_state.get("fixtures"),
                mysekai_rank=result.catalog_state.get("mysekai_rank"),
                refreshed_at_ms=result.catalog_state.get("refreshed_at_ms"),
            )
        if visit_baseline is None and result.visit_counts:
            await self.repository.set_mysekai_visit_baseline(
                qq, day, result.visit_counts
            )
        upload_id = str(uuid.uuid4())
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
        push_status, push_message = await self._push(
            upload_id=upload_id,
            qq=qq,
            game_user_id=binding["game_user_id"],
            nickname=binding.get("nickname"),
            map_png_base64=result.map_png_base64,
            card_png_base64=result.card_png_base64,
            birthday=result.birthday,
        )
        await self.repository.update_mysekai_upload_push_status(
            upload_id, push_status
        )
        if watch_on and result.birthday and result.birthday.get("targets"):
            round_key = str(result.birthday.get("round_key") or "")
            if round_key:
                # 先渲染再占位：渲染失败时不会把这一轮锁死
                rare_png = await self._render_rare(body)
                if rare_png and await self.repository.claim_mysekai_water_round(
                    binding["game_user_id"], round_key
                ):
                    await self._push_rare(
                        upload_id=upload_id,
                        qq=qq,
                        game_user_id=binding["game_user_id"],
                        nickname=binding.get("nickname"),
                        birthday=result.birthday,
                        rare_png=rare_png,
                    )
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
