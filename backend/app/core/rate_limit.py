from __future__ import annotations

import asyncio
import time


class AsyncRateLimiter:
    """Ép khoảng cách tối thiểu giữa các lần gọi liên tiếp.

    Dùng cho Nominatim (chính sách bắt buộc tối đa 1 request/giây).
    """

    def __init__(self, min_interval_s: float):
        self._min_interval = min_interval_s
        self._lock = asyncio.Lock()
        self._last_call = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_call = time.monotonic()
