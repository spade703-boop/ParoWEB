"""Render cache: repeat bodies skip the renderer, concurrent ones share it."""

from __future__ import annotations

import asyncio

import pytest

from app.services.mysekai import RenderCache


def _render(value: bytes):
    async def render() -> bytes:
        return value

    return render


def test_key_is_stable_across_dict_order() -> None:
    assert RenderCache.key({"a": 1, "b": 2}) == RenderCache.key({"b": 2, "a": 1})
    assert RenderCache.key({"a": 1}) != RenderCache.key({"a": 2})


async def test_repeat_body_returns_the_stored_png() -> None:
    # Given one rendered body
    cache = RenderCache()
    calls = 0

    async def render() -> bytes:
        nonlocal calls
        calls += 1
        return b"png"

    # When asking twice for the same key
    first = await cache.get_or_render("k", render)
    second = await cache.get_or_render("k", render)

    # Then the renderer ran once and both callers saw the same PNG
    assert first == second == b"png"
    assert calls == 1


async def test_concurrent_requests_share_one_render() -> None:
    # Given five callers racing on one key
    cache = RenderCache()
    calls = 0

    async def render() -> bytes:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return b"png"

    # When they all ask at once
    results = await asyncio.gather(
        *(cache.get_or_render("k", render) for _ in range(5))
    )

    # Then only one render ran
    assert results == [b"png"] * 5
    assert calls == 1


async def test_eviction_keeps_the_newest_entry() -> None:
    # Given a cache that holds a single 6-byte PNG
    cache = RenderCache(max_bytes=6)
    await cache.get_or_render("a", _render(b"123456"))

    # When a second one is stored
    await cache.get_or_render("b", _render(b"123456"))

    # Then the older entry is evicted
    assert cache.get("a") is None
    assert cache.get("b") == b"123456"


async def test_a_single_entry_larger_than_the_cap_is_still_served() -> None:
    cache = RenderCache(max_bytes=1)
    assert await cache.get_or_render("k", _render(b"123456")) == b"123456"


async def test_failures_are_not_cached() -> None:
    # Given a render that keeps failing
    cache = RenderCache()
    attempts = 0

    async def failing() -> bytes:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("boom")

    # When the same key is asked twice
    with pytest.raises(RuntimeError):
        await cache.get_or_render("k", failing)
    with pytest.raises(RuntimeError):
        await cache.get_or_render("k", failing)

    # Then it really rendered twice instead of replaying the error
    assert attempts == 2
