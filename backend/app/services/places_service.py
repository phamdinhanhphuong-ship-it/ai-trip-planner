from __future__ import annotations

import logging
import asyncio
from datetime import datetime, timezone
from math import atan2, cos, radians, sin, sqrt

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.core.cache import cache
from app.models.places import Place, PlacesResult
from app.services.opening_hours import is_open_at
from app.services.wikipedia_service import get_place_summary

logger = logging.getLogger(__name__)

# Thử lần lượt các mirror công cộng của Overpass; server chính hay bị quá tải/chặn IP dùng chung.
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
GEOAPIFY_URL = "https://api.geoapify.com/v2/places"
GOONG_AUTOCOMPLETE_URL = "https://rsapi.goong.io/Place/AutoComplete"
GOONG_DETAIL_URL = "https://rsapi.goong.io/Place/Detail"
# POI hiếm khi đổi vị trí/giờ mở cửa trong ngắn hạn -> cache dài để tiết kiệm quota.
CACHE_TTL_SECONDS = 6 * 60 * 60

# Ánh xạ "sở thích" người dùng chọn -> tag OSM tương ứng.
CATEGORY_TAGS: dict[str, list[tuple[str, str]]] = {
    "restaurant": [("amenity", "restaurant")],
    "cafe": [("amenity", "cafe")],
    "fast_food": [("amenity", "fast_food")],
    "museum": [("tourism", "museum")],
    "attraction": [("tourism", "attraction")],
    "viewpoint": [("tourism", "viewpoint")],
    "historic": [("historic", "*")],
    "park": [("leisure", "park")],
    "temple": [("amenity", "place_of_worship")],
}

# Chỉ các category có mapping Geoapify mới dùng được làm fallback.
GEOAPIFY_CATEGORY_MAP: dict[str, str] = {
    "restaurant": "catering.restaurant",
    "cafe": "catering.cafe",
    "fast_food": "catering.fast_food",
    "museum": "entertainment.museum",
    "attraction": "tourism.attraction",
    "viewpoint": "tourism.viewpoint",
    "park": "leisure.park",
    "temple": "religion.place_of_worship",
}

GOONG_CATEGORY_QUERY = {
    "restaurant": "nhà hàng",
    "cafe": "quán cà phê",
    "fast_food": "đồ ăn nhanh",
    "museum": "bảo tàng",
    "attraction": "điểm tham quan",
    "viewpoint": "điểm ngắm cảnh",
    "historic": "di tích lịch sử",
    "park": "công viên",
    "temple": "chùa",
}


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlambda / 2) ** 2
    return 2 * r * atan2(sqrt(a), sqrt(1 - a))


def _cache_key(lat: float, lon: float, radius_m: int, categories: list[str], cuisine: str | None = None) -> str:
    cats = ",".join(sorted(categories))
    return f"places:{round(lat, 4)}:{round(lon, 4)}:{radius_m}:{cats}:{cuisine or ''}"


def _build_overpass_query(lat: float, lon: float, radius_m: int, categories: list[str]) -> str:
    clauses = []
    for cat in categories:
        for key, value in CATEGORY_TAGS.get(cat, []):
            tag_filter = f'["{key}"]' if value == "*" else f'["{key}"="{value}"]'
            clauses.append(f'node{tag_filter}(around:{radius_m},{lat},{lon});')
            clauses.append(f'way{tag_filter}(around:{radius_m},{lat},{lon});')
    body = "\n  ".join(clauses)
    return f"[out:json][timeout:25];\n(\n  {body}\n);\nout center;"


def _infer_category(tags: dict, requested_categories: list[str]) -> str:
    for cat in requested_categories:
        for key, value in CATEGORY_TAGS.get(cat, []):
            if value == "*":
                if key in tags:
                    return cat
            elif tags.get(key) == value:
                return cat
    return "other"


async def _fetch_overpass_from(url: str, query: str) -> dict:
    headers = {"User-Agent": settings.nominatim_user_agent}
    # Timeout ngắn: mạng bị chặn/lọc thường treo kết nối thay vì trả lỗi nhanh,
    # nên không retry nhiều lần trên từng mirror — để vòng lặp mirror khác xử lý.
    async with httpx.AsyncClient(timeout=8.0) as client:
        response = await client.post(url, data={"data": query}, headers=headers)
        response.raise_for_status()
        return response.json()


async def _fetch_overpass(query: str) -> dict:
    """Thử lần lượt từng mirror; chỉ raise lỗi của mirror cuối cùng nếu tất cả đều thất bại."""
    last_exc: httpx.HTTPError | None = None
    for url in OVERPASS_MIRRORS:
        try:
            return await _fetch_overpass_from(url, query)
        except httpx.HTTPError as exc:
            logger.warning("Overpass mirror %s failed: %s", url, exc)
            last_exc = exc
    assert last_exc is not None
    raise last_exc


async def _fetch_goong_places(
    lat: float, lon: float, radius_m: int, categories: list[str], cuisine: str | None = None
) -> list[dict]:
    if not settings.goong_api_key:
        return []
    places: list[dict] = []
    async with httpx.AsyncClient(timeout=5.0) as client:
        for category in categories:
            query = GOONG_CATEGORY_QUERY.get(category, category)
            if cuisine and category in {"restaurant", "fast_food"}:
                cuisine_label = {"thai": "Thái Lan", "european": "Âu", "italian": "Ý"}.get(cuisine, cuisine)
                query = f"{query} {cuisine_label}"
            autocomplete = await client.get(
                GOONG_AUTOCOMPLETE_URL,
                params={
                    "api_key": settings.goong_api_key,
                    "input": query,
                    "location": f"{lat},{lon}",
                    "radius": radius_m,
                },
            )
            autocomplete.raise_for_status()
            # One or two details per category is enough for candidate selection.
            # More sequential detail calls quickly hit Goong's rate limit and make
            # one chat turn exceed the UI timeout.
            for prediction in autocomplete.json().get("predictions", [])[:2]:
                try:
                    detail = await client.get(
                        GOONG_DETAIL_URL,
                        params={"api_key": settings.goong_api_key, "place_id": prediction.get("place_id")},
                    )
                    detail.raise_for_status()
                    result = detail.json().get("result", {})
                    location = result.get("geometry", {}).get("location", {})
                    if location.get("lat") is None or location.get("lng") is None:
                        continue
                    distance = _haversine_m(lat, lon, float(location["lat"]), float(location["lng"]))
                    if distance <= radius_m:
                        places.append({"category": category, "result": result, "distance_m": distance, "cuisine": cuisine})
                except httpx.HTTPStatusError as exc:
                    # Keep candidates already collected; search_places can use
                    # Geoapify if Goong produced no usable result.
                    if exc.response.status_code == 429:
                        logger.warning("Goong detail rate limit for %s; moving to next category", category)
                        break
                    logger.warning("Goong detail failed for %s: %s", category, exc)
                except httpx.HTTPError as exc:
                    logger.warning("Goong detail request failed for %s: %s", category, exc)
    return places


def _goong_result_to_place(item: dict) -> Place | None:
    result = item.get("result", {})
    location = result.get("geometry", {}).get("location", {})
    if not result.get("name") or location.get("lat") is None or location.get("lng") is None:
        return None
    return Place(
        name=result["name"],
        category=item["category"],
        latitude=float(location["lat"]),
        longitude=float(location["lng"]),
        distance_m=item["distance_m"],
        address=result.get("formatted_address"),
        opening_hours_known=False,
        source="goong",
    )


def _element_to_place(element: dict, origin: tuple[float, float], category: str) -> Place | None:
    tags = element.get("tags", {})
    if element.get("type") == "node":
        lat, lon = element.get("lat"), element.get("lon")
    else:
        center = element.get("center") or {}
        lat, lon = center.get("lat"), center.get("lon")
    if lat is None or lon is None:
        return None
    name = tags.get("name")
    if not name:
        return None
    opening_hours_raw = tags.get("opening_hours")
    return Place(
        name=name,
        category=category,
        latitude=lat,
        longitude=lon,
        distance_m=_haversine_m(origin[0], origin[1], lat, lon),
        address=tags.get("addr:street"),
        opening_hours_raw=opening_hours_raw,
        opening_hours_known=opening_hours_raw is not None,
        cuisine=tags.get("cuisine"),
        source="overpass",
    )


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, max=3), reraise=True)
async def _fetch_geoapify(lat: float, lon: float, radius_m: int, categories: list[str]) -> list[dict]:
    geo_categories = [GEOAPIFY_CATEGORY_MAP[c] for c in categories if c in GEOAPIFY_CATEGORY_MAP]
    if not geo_categories or not settings.geoapify_api_key:
        return []
    params = {
        "categories": ",".join(geo_categories),
        "filter": f"circle:{lon},{lat},{radius_m}",
        "limit": 20,
        "apiKey": settings.geoapify_api_key,
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(GEOAPIFY_URL, params=params)
        response.raise_for_status()
        return response.json().get("features", [])


def _geoapify_feature_to_place(feature: dict, origin: tuple[float, float]) -> Place | None:
    props = feature.get("properties", {})
    name = props.get("name")
    lon, lat = (feature.get("geometry", {}).get("coordinates") or [None, None])
    if not name or lat is None or lon is None:
        return None
    categories = props.get("categories", [])
    category = "other"
    for cat, geo_cat in GEOAPIFY_CATEGORY_MAP.items():
        if geo_cat in categories:
            category = cat
            break
    opening_hours_raw = props.get("opening_hours")
    cuisine = props.get("cuisine") or props.get("catering.cuisine")
    if isinstance(cuisine, list):
        cuisine = ",".join(str(value) for value in cuisine)
    if not cuisine:
        cuisine_categories = [value for value in categories if value.startswith("catering.cuisine.")]
        cuisine = cuisine_categories[0].removeprefix("catering.cuisine.") if cuisine_categories else None
    return Place(
        name=name,
        category=category,
        latitude=lat,
        longitude=lon,
        distance_m=_haversine_m(origin[0], origin[1], lat, lon),
        address=props.get("address_line2") or props.get("street"),
        opening_hours_raw=opening_hours_raw,
        opening_hours_known=opening_hours_raw is not None,
        cuisine=cuisine,
        source="geoapify",
    )


async def _enrich_attractions_with_wikipedia(places: list[Place]) -> None:
    # Only landmark-like destinations are enriched; restaurants/cafes are not treated as landmarks.
    landmark_categories = {"museum", "attraction", "viewpoint", "historic", "park", "temple"}
    candidates = [place for place in places if place.category in landmark_categories][:10]
    summaries = await asyncio.gather(
        *(get_place_summary(place.name) for place in candidates),
        return_exceptions=True,
    )
    for place, summary in zip(candidates, summaries):
        if isinstance(summary, dict):
            place.description = summary.get("description")
            place.wikipedia_url = summary.get("wikipedia_url") or None


async def search_places(
    lat: float,
    lon: float,
    radius_m: int,
    categories: list[str],
    min_restaurant_results: int = 3,
    cuisine: str | None = None,
) -> PlacesResult:
    """Tìm địa điểm/quán ăn quanh (lat, lon): Goong chính, Geoapify fallback."""
    key = _cache_key(lat, lon, radius_m, categories, cuisine)
    cached = cache.get(key)
    if cached is not None:
        return PlacesResult.model_validate(cached)

    sources_used: list[str] = []
    places: list[Place] = []
    error: str | None = None

    try:
        query_categories = categories
        if cuisine:
            raw = await _fetch_goong_places(lat, lon, radius_m, query_categories, cuisine)
        else:
            raw = await _fetch_goong_places(lat, lon, radius_m, query_categories)
        for item in raw:
            place = _goong_result_to_place(item)
            if place:
                places.append(place)
        if places:
            sources_used.append("goong")
    except httpx.HTTPError as exc:
        logger.warning("Goong places request failed: %s", exc)
        error = f"Goong places lỗi hoặc hết quota: {exc}"

    needs_fallback = not places or len([p for p in places if p.category == "restaurant"]) < min_restaurant_results

    if needs_fallback and settings.geoapify_api_key:
        try:
            features = await _fetch_geoapify(lat, lon, radius_m, categories)
            for feature in features:
                place = _geoapify_feature_to_place(feature, (lat, lon))
                if place:
                    places.append(place)
            if features:
                sources_used.append("geoapify")
                error = None
        except httpx.HTTPError as exc:
            logger.warning("Geoapify fallback failed: %s", exc)

    await _enrich_attractions_with_wikipedia(places)

    result = PlacesResult(
        places=places,
        sources_used=sources_used,
        fetched_at=datetime.now(timezone.utc),
        error=error if not places else None,
    )
    cache.set(key, result.model_dump(mode="json"), expire=CACHE_TTL_SECONDS)
    return result


def filter_by_distance(places: list[Place], max_distance_m: float) -> list[Place]:
    return [p for p in places if p.distance_m is not None and p.distance_m <= max_distance_m]


def filter_by_category(places: list[Place], categories: list[str]) -> list[Place]:
    return [p for p in places if p.category in categories]


def filter_open_at(places: list[Place], when: datetime) -> tuple[list[Place], list[Place]]:
    """Trả (đang mở, không rõ giờ) — tách riêng để không loại nhầm quán thiếu dữ liệu giờ mở cửa."""
    open_places: list[Place] = []
    unknown_places: list[Place] = []
    for p in places:
        status = is_open_at(p.opening_hours_raw, when)
        if status is True:
            open_places.append(p)
        elif status is None:
            unknown_places.append(p)
    return open_places, unknown_places
