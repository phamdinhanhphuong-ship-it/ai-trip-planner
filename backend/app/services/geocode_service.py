from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.core.cache import cache
from app.core.rate_limit import AsyncRateLimiter
from app.models.places import GeocodeResult

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
GEOAPIFY_GEOCODE_URL = "https://api.geoapify.com/v1/geocode/search"
GOONG_GEOCODE_URL = "https://rsapi.goong.io/geocode"
# Địa chỉ hiếm khi đổi tọa độ -> cache dài để tránh gọi lại, tôn trọng usage policy.
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60

# Nominatim usage policy: tối đa 1 request/giây.
_rate_limiter = AsyncRateLimiter(min_interval_s=1.1)


def _cache_key(query: str) -> str:
    return f"geocode:{query.strip().lower()}"


def _coordinates_match_named_city(query: str, latitude: float, longitude: float) -> bool:
    lower = query.lower()
    regions = {
        "hà nội": (20.5, 21.6, 105.2, 106.2),
        "ha noi": (20.5, 21.6, 105.2, 106.2),
        "đà lạt": (11.7, 12.2, 107.2, 108.0),
        "da lat": (11.7, 12.2, 107.2, 108.0),
        "hồ chí minh": (10.3, 11.2, 106.3, 107.2),
        "ho chi minh": (10.3, 11.2, 106.3, 107.2),
        "sài gòn": (10.3, 11.2, 106.3, 107.2),
        "sai gon": (10.3, 11.2, 106.3, 107.2),
    }
    for marker, (min_lat, max_lat, min_lon, max_lon) in regions.items():
        if marker in lower:
            return min_lat <= latitude <= max_lat and min_lon <= longitude <= max_lon
    return True


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, max=3), reraise=True)
async def _fetch_nominatim(query: str) -> list[dict]:
    await _rate_limiter.wait()
    headers = {"User-Agent": settings.nominatim_user_agent}
    params = {"q": query, "format": "jsonv2", "limit": 1}
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(NOMINATIM_URL, params=params, headers=headers)
        response.raise_for_status()
        return response.json()


async def _fetch_goong_geocode(query: str) -> list[dict]:
    if not settings.goong_api_key:
        return []
    params = {"address": query, "api_key": settings.goong_api_key}
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(GOONG_GEOCODE_URL, params=params)
        response.raise_for_status()
        return response.json().get("results", [])


async def _fetch_geoapify_geocode(query: str) -> list[dict]:
    if not settings.geoapify_api_key:
        return []
    params = {"text": query, "limit": 1, "apiKey": settings.geoapify_api_key}
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(GEOAPIFY_GEOCODE_URL, params=params)
        response.raise_for_status()
        return response.json().get("features", [])


async def geocode(query: str) -> GeocodeResult:
    """Chuyển địa chỉ/tên địa điểm thành tọa độ.

    Goong là nguồn chính cho Việt Nam; Geoapify là fallback.
    Nominatim được giữ lại để tham khảo/test nhưng không gọi trong runtime.
    """
    key = _cache_key(query)
    cached = cache.get(key)
    if cached is not None:
        return GeocodeResult.model_validate(cached)

    goong_error: str | None = None
    try:
        raw = await _fetch_goong_geocode(query)
        if raw:
            item = raw[0]
            location = item["geometry"]["location"]
            latitude = float(location["lat"])
            longitude = float(location["lng"])
            if not _coordinates_match_named_city(query, latitude, longitude):
                goong_error = f"Goong trả tọa độ không khớp thành phố trong '{query}'"
            else:
                result = GeocodeResult(
                    query=query,
                    latitude=latitude,
                    longitude=longitude,
                    display_name=item.get("formatted_address"),
                    source="goong",
                    fetched_at=datetime.now(timezone.utc),
                )
                cache.set(key, result.model_dump(mode="json"), expire=CACHE_TTL_SECONDS)
                return result
        goong_error = f"Goong không tìm thấy địa điểm phù hợp với '{query}'"
    except httpx.HTTPError as exc:
        logger.warning("Goong request failed for %r: %s", query, exc)
        goong_error = f"Goong lỗi: {exc}"

    try:
        geoapify_results = await _fetch_geoapify_geocode(query)
        if geoapify_results:
            feature = geoapify_results[0]
            properties = feature.get("properties", {})
            coordinates = feature.get("geometry", {}).get("coordinates", [])
            if len(coordinates) >= 2:
                result = GeocodeResult(
                    query=query,
                    latitude=float(coordinates[1]),
                    longitude=float(coordinates[0]),
                    display_name=properties.get("formatted"),
                    source="geoapify",
                    fetched_at=datetime.now(timezone.utc),
                )
                cache.set(key, result.model_dump(mode="json"), expire=CACHE_TTL_SECONDS)
                return result
    except httpx.HTTPError as exc:
        logger.warning("Geoapify geocode fallback failed for %r: %s", query, exc)

    result = GeocodeResult(
        query=query,
        fetched_at=datetime.now(timezone.utc),
        error=f"{goong_error or 'Goong không có dữ liệu'} (đã thử fallback Geoapify nhưng cũng không có kết quả)",
    )

    return result
