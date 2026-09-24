from __future__ import annotations

from datetime import datetime, timezone

from app.core.cache import cache


def _month_key(prefix: str) -> str:
    now = datetime.now(timezone.utc)
    return f"{prefix}:{now.year}-{now.month:02d}"


def get_monthly_count(prefix: str) -> int:
    return cache.get(_month_key(prefix), default=0)


def increment_monthly_count(prefix: str) -> int:
    """Tăng bộ đếm request/tháng cho 1 API free-tier, tự reset khi sang tháng mới."""
    key = _month_key(prefix)
    count = cache.get(key, default=0) + 1
    cache.set(key, count, expire=32 * 24 * 60 * 60)
    return count
