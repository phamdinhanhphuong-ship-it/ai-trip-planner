from datetime import date

import httpx
import pytest

from app.services import weather_service

SAMPLE_RESPONSE = {
    "hourly": {
        "time": ["2026-09-21T18:00", "2026-09-21T20:00"],
        "temperature_2m": [29.5, 27.1],
        "precipitation": [0.0, 1.2],
        "precipitation_probability": [10, 40],
        "weathercode": [1, 61],
        "windspeed_10m": [12.0, 15.5],
    }
}


@pytest.mark.asyncio
async def test_get_hourly_weather_parses_and_caches(monkeypatch):
    call_count = {"n": 0}

    async def fake_fetch(lat, lon, target_date):
        call_count["n"] += 1
        return SAMPLE_RESPONSE

    monkeypatch.setattr(weather_service, "_fetch_open_meteo", fake_fetch)
    weather_service.cache.clear()

    result = await weather_service.get_hourly_weather(10.7769, 106.7009, date(2026, 9, 21))

    assert result.error is None
    assert len(result.hourly) == 2
    assert result.hourly[0].temperature_c == 29.5
    assert result.source == "open-meteo"

    # Second call with the same key must hit the cache, not call fetch again.
    await weather_service.get_hourly_weather(10.7769, 106.7009, date(2026, 9, 21))
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_get_hourly_weather_surfaces_error_on_failure(monkeypatch):
    async def fake_fetch(lat, lon, target_date):
        raise httpx.HTTPError("boom")

    monkeypatch.setattr(weather_service, "_fetch_open_meteo", fake_fetch)
    weather_service.cache.clear()

    result = await weather_service.get_hourly_weather(1.0, 2.0, date(2026, 9, 21))

    assert result.error is not None
    assert result.hourly == []


@pytest.mark.asyncio
async def test_get_daily_environment_returns_sunset_and_aqi(monkeypatch):
    async def fake_forecast(lat, lon, target_date):
        return {"daily": {"sunset": ["2026-09-21T18:05"]}}

    async def fake_air_quality(lat, lon, target_date):
        return {"hourly": {"us_aqi": [42, 75, None]}}

    monkeypatch.setattr(weather_service, "_fetch_open_meteo", fake_forecast)
    monkeypatch.setattr(weather_service, "_fetch_air_quality", fake_air_quality)

    result = await weather_service.get_daily_environment(10.77, 106.70, date(2026, 9, 21))

    assert result.sunset.hour == 18
    assert result.sunset.minute == 5
    assert result.air_quality_aqi == 75
    assert result.air_quality_label == "Trung bình"


def test_get_weather_at_hour_matches_exact_hour():
    from datetime import datetime

    from app.models.weather import HourlyWeather, WeatherResult

    result = WeatherResult(
        latitude=1.0,
        longitude=2.0,
        fetched_at=datetime(2026, 9, 21, 12, 0),
        hourly=[
            HourlyWeather(time=datetime(2026, 9, 21, 18, 0), temperature_c=30.0),
            HourlyWeather(time=datetime(2026, 9, 21, 20, 0), temperature_c=27.0),
        ],
    )

    point = weather_service.get_weather_at_hour(result, datetime(2026, 9, 21, 18, 30))
    assert point is not None
    assert point.temperature_c == 30.0

    assert weather_service.get_weather_at_hour(result, datetime(2026, 9, 21, 23, 0)) is None
