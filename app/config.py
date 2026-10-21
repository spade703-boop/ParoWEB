from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import secrets


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _csv_env(name: str, default: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in os.getenv(name, default).split(",") if item.strip())


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed < 0:
        raise ValueError(f"{name} must be non-negative")
    return parsed


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str
    project_root: Path
    content_dir: Path
    images_dir: Path
    announcements_path: Path
    database_path: Path
    cookie_secure: bool
    allowed_hosts: tuple[str, ...]
    allowed_origins: tuple[str, ...]
    draw_cooldown_seconds: int
    stats_cache_seconds: int
    mysekai_password: str
    mysekai_session_secret: str
    mysekai_service_url: str
    mysekai_haruki_base: str
    mysekai_asset_base: str
    mysekai_master_base: str
    mysekai_bot_token: str
    mysekai_push_url: str
    mysekai_push_token: str

    @classmethod
    def from_env(cls) -> "Settings":
        environment = os.getenv("PARO_ENV", "development").strip().lower()
        content_dir = Path(os.getenv("PARO_CONTENT_DIR", PROJECT_ROOT / "content")).resolve()
        images_override = os.getenv("PARO_IMAGES_DIR")
        images_dir = Path(images_override).resolve() if images_override else content_dir / "images"
        announcements_path = Path(
            os.getenv("PARO_ANNOUNCEMENTS_PATH", PROJECT_ROOT / "data" / "announcements.json")
        ).resolve()
        database_path = Path(os.getenv("PARO_DATABASE_PATH", PROJECT_ROOT / "data" / "paro_web.sqlite3")).resolve()
        return cls(
            environment=environment,
            project_root=PROJECT_ROOT,
            content_dir=content_dir,
            images_dir=images_dir,
            announcements_path=announcements_path,
            database_path=database_path,
            cookie_secure=_bool_env("PARO_COOKIE_SECURE", environment == "production"),
            allowed_hosts=_csv_env("PARO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver"),
            allowed_origins=_csv_env(
                "PARO_ALLOWED_ORIGINS",
                "http://localhost:8000,http://127.0.0.1:8000,http://testserver",
            ),
            draw_cooldown_seconds=_int_env("PARO_DRAW_COOLDOWN_SECONDS", 5),
            stats_cache_seconds=_int_env("PARO_STATS_CACHE_SECONDS", 60),
            mysekai_password=os.getenv("PARO_MYSEKAI_PASSWORD", "").strip(),
            mysekai_session_secret=os.getenv("PARO_MYSEKAI_SESSION_SECRET", "").strip()
            or secrets.token_urlsafe(32),
            mysekai_service_url=os.getenv(
                "PARO_MYSEKAI_SERVICE_URL", "http://127.0.0.1:8765"
            ).rstrip("/"),
            mysekai_haruki_base=os.getenv(
                "PARO_MYSEKAI_HARUKI_BASE",
                "https://toolbox-api-direct.haruki.seiunx.com",
            ).rstrip("/"),
            mysekai_asset_base=os.getenv(
                "PARO_MYSEKAI_ASSET_BASE",
                "https://sekai-assets.haruki.seiunx.com",
            ).rstrip("/"),
            mysekai_master_base=os.getenv(
                "PARO_MYSEKAI_MASTER_BASE",
                "https://sekai-api-cdn.haruki.seiunx.com",
            ).rstrip("/"),
            mysekai_bot_token=os.getenv("PARO_MYSEKAI_BOT_TOKEN", "").strip(),
            mysekai_push_url=os.getenv("PARO_MYSEKAI_PUSH_URL", "").strip(),
            mysekai_push_token=os.getenv("PARO_MYSEKAI_PUSH_TOKEN", "").strip(),
        )
