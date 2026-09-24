from __future__ import annotations

import logging
from urllib.parse import quote

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.cache import cache

logger = logging.getLogger(__name__)

WIKIPEDIA_SEARCH_URL = "https://vi.wikipedia.org/w/rest.php/v1/search/page"
WIKIPEDIA_SUMMARY_URL = "https://vi.wikipedia.org/api/rest_v1/page/summary"
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, max=3), reraise=True)
async def _fetch_summary(query: str) -> dict | None:
    headers = {"User-Agent": "guidepass-ai-test/0.1"}
    async with httpx.AsyncClient(timeout=8.0, headers=headers) as client:
        search = await client.get(WIKIPEDIA_SEARCH_URL, params={"q": query, "limit": 1})
        search.raise_for_status()
        pages = search.json().get("pages", [])
        if not pages:
            return None
        title = pages[0].get("title")
        if not title:
            return None
        summary = await client.get(f"{WIKIPEDIA_SUMMARY_URL}/{quote(title, safe='')}" )
        summary.raise_for_status()
        return summary.json()


async def get_place_summary(place_name: str) -> dict[str, str] | None:
    """Return a short Vietnamese Wikipedia summary when an article exists."""
    key = f"wikipedia:vi:{place_name.strip().lower()}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        raw = await _fetch_summary(place_name)
    except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError) as exc:
        logger.warning("Wikipedia lookup failed for %r: %s", place_name, exc)
        return None
    if not raw or not raw.get("extract"):
        return None
    result = {
        "description": raw["extract"],
        "wikipedia_url": raw.get("content_urls", {}).get("desktop", {}).get("page", ""),
    }
    cache.set(key, result, expire=CACHE_TTL_SECONDS)
    return result
