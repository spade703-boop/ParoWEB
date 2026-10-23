from __future__ import annotations

from pathlib import Path

from app.repositories.sqlite import SQLiteRepository


async def _repo(tmp_path: Path) -> SQLiteRepository:
    repository = SQLiteRepository(tmp_path / "test.sqlite3")
    await repository.initialize()
    return repository


async def test_claim_is_once_per_window(tmp_path) -> None:
    repository = await _repo(tmp_path)
    assert await repository.claim_mysekai_push_window("g1", "2026-10-23-05", "u1") is True
    assert await repository.claim_mysekai_push_window("g1", "2026-10-23-05", "u2") is False
    assert await repository.claim_mysekai_push_window("g1", "2026-10-23-17", "u3") is True
    assert await repository.claim_mysekai_push_window("g2", "2026-10-23-05", "u4") is True


async def test_release_allows_a_retry(tmp_path) -> None:
    repository = await _repo(tmp_path)
    await repository.claim_mysekai_push_window("g1", "2026-10-23-05", "u1")

    await repository.release_mysekai_push_window("g1", "2026-10-23-05", "u1")

    assert await repository.claim_mysekai_push_window("g1", "2026-10-23-05", "u2") is True


async def test_release_ignores_a_different_upload(tmp_path) -> None:
    repository = await _repo(tmp_path)
    await repository.claim_mysekai_push_window("g1", "2026-10-23-05", "u1")

    await repository.release_mysekai_push_window("g1", "2026-10-23-05", "u2")

    assert await repository.claim_mysekai_push_window("g1", "2026-10-23-05", "u3") is False
