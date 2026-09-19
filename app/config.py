from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _csv_env(name: str, default: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in os.getenv(name, default).split(",") if item.strip())


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str
    project_root: Path
    content_dir: Path
    database_path: Path
    cookie_secure: bool
    allowed_hosts: tuple[str, ...]
    allowed_origins: tuple[str, ...]

    @classmethod
    def from_env(cls) -> "Settings":
        environment = os.getenv("PARO_ENV", "development").strip().lower()
        content_dir = Path(os.getenv("PARO_CONTENT_DIR", PROJECT_ROOT / "content")).resolve()
        database_path = Path(os.getenv("PARO_DATABASE_PATH", PROJECT_ROOT / "data" / "paro_web.sqlite3")).resolve()
        return cls(
            environment=environment,
            project_root=PROJECT_ROOT,
            content_dir=content_dir,
            database_path=database_path,
            cookie_secure=_bool_env("PARO_COOKIE_SECURE", environment == "production"),
            allowed_hosts=_csv_env("PARO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver"),
            allowed_origins=_csv_env(
                "PARO_ALLOWED_ORIGINS",
                "http://localhost:8000,http://127.0.0.1:8000,http://testserver",
            ),
        )

