from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import logging
from pathlib import Path
import threading


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Announcement:
    id: str
    published_at: str
    title: str
    summary: str
    details: tuple[str, ...]
    tags: tuple[str, ...]
    highlight: bool

    def as_dict(self) -> dict:
        return asdict(self)


class AnnouncementService:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self._items: tuple[Announcement, ...] = ()
        self._version = "empty"
        self._signature: tuple[int, int] | None = None
        self._lock = threading.Lock()

    @property
    def version(self) -> str:
        self.snapshot
        return self._version

    @property
    def snapshot(self) -> tuple[Announcement, ...]:
        self.reload_if_changed()
        return self._items

    def _file_signature(self) -> tuple[int, int] | None:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return None
        return stat.st_mtime_ns, stat.st_size

    def load(self) -> tuple[Announcement, ...]:
        with self._lock:
            raw_bytes = self.path.read_bytes()
            items = self._parse(raw_bytes)
            self._items = items
            self._version = hashlib.sha256(raw_bytes).hexdigest()[:16]
            self._signature = self._file_signature()
            return items

    def reload_if_changed(self) -> bool:
        signature = self._file_signature()
        if signature == self._signature:
            return False
        if signature is None:
            self._items = ()
            self._version = "empty"
            self._signature = None
            return True
        with self._lock:
            if signature == self._signature:
                return False
            try:
                raw_bytes = self.path.read_bytes()
                items = self._parse(raw_bytes)
            except Exception:
                logger.exception("Announcement reload failed; keeping the previous valid snapshot")
                return False
            self._items = items
            self._version = hashlib.sha256(raw_bytes).hexdigest()[:16]
            self._signature = signature
            return True

    @staticmethod
    def _parse(raw_bytes: bytes) -> tuple[Announcement, ...]:
        try:
            raw = json.loads(raw_bytes)
        except json.JSONDecodeError as exc:
            raise ValueError("更新公告 JSON 无法解析") from exc
        entries = raw.get("items") if isinstance(raw, dict) else raw
        if not isinstance(entries, list):
            raise ValueError("更新公告必须是列表或包含 items 列表的对象")

        announcements: list[Announcement] = []
        seen_ids: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("更新公告条目必须是对象")
            announcement_id = str(entry.get("id") or "").strip()
            title = str(entry.get("title") or "").strip()
            published_at = str(entry.get("published_at") or "").strip()
            summary = str(entry.get("summary") or "").strip()
            if not announcement_id or announcement_id in seen_ids:
                raise ValueError("更新公告 ID 不能为空或重复")
            if not title or not published_at or not summary:
                raise ValueError("更新公告必须包含日期、标题和摘要")
            raw_details = entry.get("details", [])
            raw_tags = entry.get("tags", [])
            if not isinstance(raw_details, list) or not isinstance(raw_tags, list):
                raise ValueError("更新公告 details 和 tags 必须是列表")
            details = tuple(str(item).strip() for item in raw_details if str(item).strip())
            tags = tuple(str(item).strip() for item in raw_tags if str(item).strip())
            if not details:
                raise ValueError("更新公告至少需要一条详细说明")
            announcements.append(
                Announcement(
                    id=announcement_id,
                    published_at=published_at,
                    title=title,
                    summary=summary,
                    details=details,
                    tags=tags,
                    highlight=bool(entry.get("highlight", False)),
                )
            )
            seen_ids.add(announcement_id)
        return tuple(announcements)

    def payload(self) -> dict:
        return {"version": self.version, "items": [item.as_dict() for item in self.snapshot]}
