from __future__ import annotations

from collections import defaultdict, deque
import time


class SlidingWindowLimiter:
    def __init__(self, *, limit: int, window_seconds: int = 60) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._requests: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        requests = self._requests[key]
        while requests and now - requests[0] >= self.window_seconds:
            requests.popleft()
        if len(requests) >= self.limit:
            retry_after = max(1, int(self.window_seconds - (now - requests[0])) + 1)
            return False, retry_after
        requests.append(now)
        return True, 0

