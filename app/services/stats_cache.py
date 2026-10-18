from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import time
from typing import Awaitable, Callable


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class StatsCache:
    """社区统计的进程内短期缓存。

    一次全表聚合在本地 11 万行上要 2.4~2.7 秒，前端又是 60 秒轮询的，不缓存的话
    每个停留用户每分钟都要重新扫一遍 —— 十个人开着这页就是每分钟十次全表扫描。
    缓存之后同一份结果 60 秒内所有人共用，代价是数据最多滞后 60 秒，
    对"趣味统计"这种东西无所谓。

    只缓存仓储层返回的原始数据，头像 URL 仍然每次现拼：换图要立刻生效，
    不能让旧的 ?v= 版本号在榜单上被关 60 秒。

    抽卡会让统计变脏，所以落地之后要调 invalidate()。最坏情况（抽得比问得还勤）
    会退化成没有缓存，结果不会算错。
    """

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = max(0.0, float(ttl_seconds))
        self._lock = asyncio.Lock()
        self._value: dict | None = None
        self._stored_at = 0.0
        # 这份数据真正算出来的时刻（UTC ISO）。页面上的「更新于」要用它，
        # 不能用响应到达浏览器的时间 —— 命中缓存时后者永远是"刚刚"，
        # 看着像实时，其实可能是 TTL 快走完时的一份旧结果。
        self._generated_at: str | None = None

    @property
    def enabled(self) -> bool:
        return self._ttl > 0

    async def get(self, producer: Callable[[], Awaitable[dict]]) -> tuple[dict, str]:
        """返回 (数据, 这份数据算出来的时刻)。"""
        if not self.enabled:
            return await producer(), _utc_now_iso()
        cached = self._fresh_value()
        if cached is not None:
            return cached, self._generated_at or _utc_now_iso()
        # 并发下只让第一个进去的算，等在锁上的拿它算好的结果；
        # 进锁后再看一眼，因为等的这段时间可能已经有人算完了
        async with self._lock:
            cached = self._fresh_value()
            if cached is not None:
                return cached, self._generated_at or _utc_now_iso()
            value = await producer()
            self._value = value
            self._stored_at = time.monotonic()
            self._generated_at = _utc_now_iso()
            return value, self._generated_at

    def invalidate(self) -> None:
        self._value = None
        self._stored_at = 0.0
        self._generated_at = None

    def _fresh_value(self) -> dict | None:
        if self._value is None:
            return None
        if time.monotonic() - self._stored_at >= self._ttl:
            return None
        return self._value
