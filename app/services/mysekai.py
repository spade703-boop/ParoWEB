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

import base64
import hashlib
import hmac
import io
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from PIL import Image

from app.config import Settings
from app.repositories.sqlite import SQLiteRepository


COOKIE_NAME = "paro_mysekai"
SESSION_TTL_SECONDS = 12 * 60 * 60
MAX_BODY_BYTES = 4 * 1024 * 1024
AVATAR_SIZE = 96
CARDS_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60


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


_CN = timezone(timedelta(hours=8))
#: JP 7.0.0 raised the gate cap from 40 to 70; the render side reads the real cap
#: from masterdata, this constant only decides which gate Haruki would default to.
GATE_MAX_LEVEL = 70


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
        self, game_user_id: str, server: str = "cn"
    ) -> MysekaiGateData:
        """Read the gate level and material holdings from Haruki's public suite.

        The public endpoint's ``key`` filter rejects ``userMysekai*`` fields
        (404), so this asks for the whole response and keeps only what the gate
        image needs.
        """
        if not game_user_id.isdigit():
            raise AccountNotFound("游戏 ID 必须是数字")
        url = (
            f"{self.settings.mysekai_haruki_base}/api/public/"
            f"{server}/suite/{game_user_id}"
        )
        try:
            async with self._client(timeout=60.0) as client:
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
        if response.status_code != 200:
            raise UpstreamError("出图服务暂时不可用，请稍后再试")
        return response.content

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
