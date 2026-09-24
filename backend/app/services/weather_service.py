from __future__ import annotations

import logging
from datetime import date, datetime, timezone

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.cache import cache
from app.models.weather import DailyEnvironment, HourlyWeather, WeatherResult

logger = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
# Open-Meteo forecasts refresh roughly hourly, so a shorter TTL keeps data fresh.
CACHE_TTL_SECONDS = 30 * 60


def _cache_key(lat: float, lon: float, target_date: date) -> str:
    return f"weather:{round(lat, 3)}:{round(lon, 3)}:{target_date.isoformat()}"


def _safe_get(block: dict, field: str, index: int):
    values = block.get(field)
    if not values or index >= len(values):
        return None
    return values[index]


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, max=4), reraise=True)
async def _fetch_open_meteo(lat: float, lon: float, target_date: date) -> dict:
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,apparent_temperature,precipitation,precipitation_probability,weathercode,windspeed_10m",
        "timezone": "auto",
        "start_date": target_date.isoformat(),
        "end_date": target_date.isoformat(),
        "daily": "sunset",
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(OPEN_METEO_URL, params=params)
        response.raise_for_status()
        return response.json()


async def get_hourly_weather(lat: float, lon: float, target_date: date) -> WeatherResult:
    """Trả dự báo thời tiết theo giờ cho 1 ngày tại 1 tọa độ, có cache và nhãn nguồn dữ liệu."""
    key = _cache_key(lat, lon, target_date)
    cached = cache.get(key)
    if cached is not None:
        return WeatherResult.model_validate(cached)

    try:
        raw = await _fetch_open_meteo(lat, lon, target_date)
    except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError) as exc:
        logger.warning("Open-Meteo request failed for (%s, %s): %s", lat, lon, exc)
        # Explicit error surfaced instead of silently returning empty/fake data.
        return WeatherResult(
            latitude=lat,
            longitude=lon,
            hourly=[],
            source="open-meteo",
            fetched_at=datetime.now(timezone.utc),
            error=f"Không lấy được dữ liệu thời tiết từ Open-Meteo: {exc}",
        )

    hourly_block = raw.get("hourly", {})
    times = hourly_block.get("time", [])
    result = WeatherResult(
        latitude=lat,
        longitude=lon,
        hourly=[
            HourlyWeather(
                time=datetime.fromisoformat(times[i]),
                temperature_c=_safe_get(hourly_block, "temperature_2m", i),
                apparent_temperature_c=_safe_get(hourly_block, "apparent_temperature", i),
                precipitation_mm=_safe_get(hourly_block, "precipitation", i),
                precipitation_probability_pct=_safe_get(hourly_block, "precipitation_probability", i),
                weather_code=_safe_get(hourly_block, "weathercode", i),
                wind_speed_kmh=_safe_get(hourly_block, "windspeed_10m", i),
            )
            for i in range(len(times))
        ],
        source="open-meteo",
        fetched_at=datetime.now(timezone.utc),
    )
    cache.set(key, result.model_dump(mode="json"), expire=CACHE_TTL_SECONDS)
    return result


def get_weather_at_hour(result: WeatherResult, target_hour: datetime) -> HourlyWeather | None:
    """Tìm điểm dữ liệu giờ khớp với target_hour (bỏ qua phút/giây)."""
    rounded_target = target_hour.replace(minute=0, second=0, microsecond=0)
    for point in result.hourly:
        if point.time.replace(minute=0, second=0, microsecond=0) == rounded_target:
            return point
    return None


def _aqi_label(aqi: int | None) -> str | None:
    if aqi is None:
        return None
    if aqi <= 50:
        return "Tốt"
    if aqi <= 100:
        return "Trung bình"
    if aqi <= 150:
        return "Không tốt cho nhóm nhạy cảm"
    if aqi <= 200:
        return "Không tốt"
    if aqi <= 300:
        return "Rất không tốt"
    return "Nguy hại"


async def _fetch_air_quality(lat: float, lon: float, target_date: date) -> dict:
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "us_aqi",
        "timezone": "auto",
        "start_date": target_date.isoformat(),
        "end_date": target_date.isoformat(),
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(OPEN_METEO_AIR_QUALITY_URL, params=params)
        response.raise_for_status()
        return response.json()


async def get_daily_environment(lat: float, lon: float, target_date: date) -> DailyEnvironment:
    """Fetch optional day-level sunset and AQI without blocking itinerary creation."""
    sunset: datetime | None = None
    aqi: int | None = None
    errors: list[str] = []
    try:
        forecast = await _fetch_open_meteo(lat, lon, target_date)
        sunset_values = forecast.get("daily", {}).get("sunset", [])
        if sunset_values:
            sunset = datetime.fromisoformat(sunset_values[0])
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        errors.append(f"sunset: {exc}")
    try:
        air_quality = await _fetch_air_quality(lat, lon, target_date)
        values = [value for value in air_quality.get("hourly", {}).get("us_aqi", []) if value is not None]
        if values:
            aqi = round(max(values))
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        errors.append(f"AQI: {exc}")
    return DailyEnvironment(
        date=datetime.combine(target_date, datetime.min.time()),
        sunset=sunset,
        air_quality_aqi=aqi,
        air_quality_label=_aqi_label(aqi),
        source="Open-Meteo Forecast + Open-Meteo Air Quality",
        error="; ".join(errors) if errors else None,
    )
