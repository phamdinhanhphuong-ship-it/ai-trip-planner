from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.core.cache import cache
from app.core.quota import get_monthly_count, increment_monthly_count
from app.models.routing import RouteResult, TravelMode

logger = logging.getLogger(__name__)

TOMTOM_URL_TEMPLATE = "https://api.tomtom.com/routing/1/calculateRoute/{coords}/json"
OSRM_URL_TEMPLATE = "https://router.project-osrm.org/route/v1/driving/{coords}"
GOONG_URL = "https://rsapi.goong.io/Direction"
MAPBOX_WALKING_URL = "https://api.mapbox.com/directions/v5/mapbox/walking/{coords}"

# TomTom Routing API free tier hiện tại: 20.000 request/tháng (đã xác nhận trên trang pricing chính thức).
TOMTOM_MONTHLY_LIMIT = 20_000
# Chừa biên an toàn để không lỡ vượt quota giữa lúc demo/chấm bài.
TOMTOM_SAFETY_MARGIN = 500
TOMTOM_QUOTA_PREFIX = "tomtom_routing_quota"

# Kết quả phụ thuộc traffic thời gian thực -> cache ngắn hạn, làm tròn giờ khởi hành về mốc 15 phút
# để tăng tỷ lệ cache-hit (đỡ tốn quota) mà không làm sai lệch nhiều so với giờ người dùng yêu cầu.
CACHE_TTL_SECONDS = 10 * 60


def _round_depart_at(depart_at: datetime) -> datetime:
    minute = (depart_at.minute // 15) * 15
    return depart_at.replace(minute=minute, second=0, microsecond=0)


def _cache_key(origin: tuple[float, float], destination: tuple[float, float], depart_at: datetime, travel_mode: TravelMode) -> str:
    rounded = _round_depart_at(depart_at)
    return (
        f"route:{round(origin[0], 4)}:{round(origin[1], 4)}:"
        f"{round(destination[0], 4)}:{round(destination[1], 4)}:{rounded.isoformat()}:{travel_mode}"
    )


def tomtom_quota_available() -> bool:
    return get_monthly_count(TOMTOM_QUOTA_PREFIX) < (TOMTOM_MONTHLY_LIMIT - TOMTOM_SAFETY_MARGIN)


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, max=4), reraise=True)
async def _fetch_tomtom(origin: tuple[float, float], destination: tuple[float, float], depart_at: datetime) -> dict:
    coords = f"{origin[0]},{origin[1]}:{destination[0]},{destination[1]}"
    url = TOMTOM_URL_TEMPLATE.format(coords=coords)
    params = {
        "key": settings.tomtom_api_key,
        "departAt": depart_at.isoformat(timespec="seconds"),
        "traffic": "true",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, max=3), reraise=True)
async def _fetch_osrm(origin: tuple[float, float], destination: tuple[float, float]) -> dict:
    # OSRM dùng thứ tự lon,lat (ngược với TomTom).
    coords = f"{origin[1]},{origin[0]};{destination[1]},{destination[0]}"
    url = OSRM_URL_TEMPLATE.format(coords=coords)
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url, params={"overview": "full", "geometries": "geojson"})
        response.raise_for_status()
        return response.json()


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, max=3), reraise=True)
async def _fetch_goong(origin: tuple[float, float], destination: tuple[float, float], travel_mode: TravelMode) -> dict:
    vehicle = "bike" if travel_mode == "motorcycle" else "car"
    params = {
        "origin": f"{origin[0]},{origin[1]}",
        "destination": f"{destination[0]},{destination[1]}",
        "vehicle": vehicle,
        "api_key": settings.goong_api_key,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(GOONG_URL, params=params)
        response.raise_for_status()
        return response.json()


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, max=3), reraise=True)
async def _fetch_mapbox_walking(origin: tuple[float, float], destination: tuple[float, float]) -> dict:
    coords = f"{origin[1]},{origin[0]};{destination[1]},{destination[0]}"
    url = MAPBOX_WALKING_URL.format(coords=coords)
    params = {"access_token": settings.mapbox_api_key, "overview": "false"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()


def _goong_summary(raw: dict) -> tuple[float, float]:
    leg = raw["routes"][0]["legs"][0]
    return float(leg["distance"]["value"]), float(leg["duration"]["value"])


def _mapbox_summary(raw: dict) -> tuple[float, float]:
    route = raw["routes"][0]
    return float(route["distance"]), float(route["duration"])


def _tomtom_geometry(raw: dict) -> list[tuple[float, float]] | None:
    points = raw.get("routes", [{}])[0].get("legs", [{}])[0].get("points", [])
    geometry = [(float(point["latitude"]), float(point["longitude"])) for point in points]
    return geometry or None


def _osrm_geometry(raw: dict) -> list[tuple[float, float]] | None:
    coordinates = raw.get("routes", [{}])[0].get("geometry", {}).get("coordinates", [])
    geometry = [(float(lonlat[1]), float(lonlat[0])) for lonlat in coordinates if len(lonlat) >= 2]
    return geometry or None


async def get_route(
    origin: tuple[float, float],
    destination: tuple[float, float],
    depart_at: datetime,
    travel_mode: TravelMode = "car",
) -> RouteResult:
    """Tính thời gian/khoảng cách A->B theo giờ khởi hành.

    Ô tô ưu tiên TomTom traffic thật rồi OSRM; xe máy dùng Goong Directions.
    Walking dùng Mapbox walking; nếu thiếu key hoặc Mapbox lỗi thì trả lỗi rõ ràng.
    """
    key = _cache_key(origin, destination, depart_at, travel_mode)
    cached = cache.get(key)
    if cached is not None:
        return RouteResult.model_validate(cached)

    if travel_mode == "walking":
        if not settings.mapbox_api_key:
            return RouteResult(
                origin=origin, destination=destination, depart_at=depart_at,
                travel_mode=travel_mode, traffic_aware=False, source="none",
                fetched_at=datetime.now(timezone.utc),
                error="Chưa cấu hình MAPBOX_API_KEY cho định tuyến đi bộ.",
            )
        try:
            distance_m, duration_s = _mapbox_summary(await _fetch_mapbox_walking(origin, destination))
            result = RouteResult(
                origin=origin, destination=destination, depart_at=depart_at,
                travel_mode=travel_mode, distance_m=distance_m, duration_s=duration_s,
                traffic_aware=False, source="mapbox", fetched_at=datetime.now(timezone.utc),
            )
            cache.set(key, result.model_dump(mode="json"), expire=CACHE_TTL_SECONDS)
            return result
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            logger.warning("Mapbox walking routing failed: %s", exc)
            return RouteResult(
                origin=origin, destination=destination, depart_at=depart_at,
                travel_mode=travel_mode, traffic_aware=False, source="none",
                fetched_at=datetime.now(timezone.utc),
                error=f"Không tính được lộ trình đi bộ từ Mapbox: {exc}",
            )

    if travel_mode == "motorcycle" and settings.goong_api_key:
        try:
            distance_m, duration_s = _goong_summary(await _fetch_goong(origin, destination, travel_mode))
            result = RouteResult(
                origin=origin, destination=destination, depart_at=depart_at,
                travel_mode=travel_mode, distance_m=distance_m, duration_s=duration_s,
                traffic_aware=False, source="goong", fetched_at=datetime.now(timezone.utc),
            )
            cache.set(key, result.model_dump(mode="json"), expire=CACHE_TTL_SECONDS)
            return result
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            logger.warning("Goong motorcycle routing failed, falling back to no-data: %s", exc)

    if travel_mode == "car" and settings.tomtom_api_key and tomtom_quota_available():
        try:
            raw = await _fetch_tomtom(origin, destination, depart_at)
            increment_monthly_count(TOMTOM_QUOTA_PREFIX)
            summary = raw["routes"][0]["summary"]
            result = RouteResult(
                origin=origin,
                destination=destination,
                depart_at=depart_at,
                travel_mode=travel_mode,
                distance_m=summary["lengthInMeters"],
                duration_s=summary["travelTimeInSeconds"],
                traffic_aware=True,
                source="tomtom",
                fetched_at=datetime.now(timezone.utc),
                geometry=_tomtom_geometry(raw),
            )
            cache.set(key, result.model_dump(mode="json"), expire=CACHE_TTL_SECONDS)
            return result
        except (httpx.HTTPError, KeyError, IndexError) as exc:
            logger.warning("TomTom routing failed, falling back to OSRM: %s", exc)

    try:
        raw = await _fetch_osrm(origin, destination)
        route = raw["routes"][0]
        result = RouteResult(
            origin=origin,
            destination=destination,
            depart_at=depart_at,
            travel_mode=travel_mode,
            distance_m=route["distance"],
            duration_s=route["duration"],
            traffic_aware=False,
            source="osrm",
            fetched_at=datetime.now(timezone.utc),
            geometry=_osrm_geometry(raw),
        )
        cache.set(key, result.model_dump(mode="json"), expire=CACHE_TTL_SECONDS)
        return result
    except (httpx.HTTPError, KeyError, IndexError) as exc:
        logger.warning("OSRM routing also failed: %s", exc)
        return RouteResult(
            origin=origin,
            destination=destination,
            depart_at=depart_at,
            travel_mode=travel_mode,
            traffic_aware=False,
            source="none",
            fetched_at=datetime.now(timezone.utc),
            error=f"Không tính được lộ trình (TomTom và OSRM đều lỗi): {exc}",
        )


def describe_source(result: RouteResult) -> str:
    """Câu mô tả nguồn dữ liệu để LLM/agent bắt buộc gắn kèm khi trả lời."""
    if result.source == "tomtom":
        return f"{result.travel_mode}, traffic thực lúc {result.depart_at.strftime('%H:%M %d/%m')} (nguồn: TomTom)"
    if result.source == "goong":
        return f"{result.travel_mode} (nguồn: Goong, không có traffic thời gian thực)"
    if result.source == "mapbox":
        return f"{result.travel_mode} (nguồn: Mapbox, không có traffic thời gian thực)"
    if result.source == "osrm":
        return "ước lượng tĩnh, KHÔNG phải traffic thực (nguồn: OSRM, fallback)"
    return "không xác định được nguồn dữ liệu"


async def compare_departure_times(
    origin: tuple[float, float],
    destination: tuple[float, float],
    depart_times: list[datetime],
    travel_mode: TravelMode = "car",
) -> list[RouteResult]:
    """Tính route cho nhiều mốc giờ khởi hành để so sánh (vd. kịch bản 18h vs 20h).

    Cảnh báo rõ nếu các mốc phải dùng khác nguồn dữ liệu (traffic thật vs ước lượng tĩnh),
    vì khi đó phép so sánh không còn công bằng/chính xác.
    """
    results = [await get_route(origin, destination, t, travel_mode) for t in depart_times]
    sources_used = {r.source for r in results}
    if len(sources_used) > 1:
        warning = (
            " [Cảnh báo: các mốc giờ dùng khác nguồn dữ liệu "
            f"({', '.join(sorted(sources_used))}) — so sánh có thể không công bằng]"
        )
        for r in results:
            r.error = (r.error or "") + warning
    return results
