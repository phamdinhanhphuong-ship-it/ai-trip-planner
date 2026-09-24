from datetime import datetime, timezone

import pytest

from app.models.places import Place, PlacesResult
from app.models.weather import HourlyWeather, WeatherResult
from app.services import places_service, trip_planner, weather_service

ORIGIN = (10.7727, 106.6980)
WHEN = datetime(2026, 9, 21, 18, 0)


def _place(name, distance_m, opening_hours_raw=None, opening_hours_known=False, category="restaurant"):
    return Place(
        name=name, category=category, latitude=10.78, longitude=106.70,
        distance_m=distance_m, opening_hours_raw=opening_hours_raw, opening_hours_known=opening_hours_known,
    )


@pytest.mark.asyncio
async def test_plan_nearby_visit_filters_distance_and_opening_hours(monkeypatch):
    places = [
        _place("Gan va mo", 200, "08:00-22:00", True),
        _place("Gan nhung dong", 300, "23:00-23:59", True),
        _place("Gan nhung khong ro gio", 400, None, False),
        _place("Qua xa", 5000, "08:00-22:00", True),
    ]

    async def fake_search(lat, lon, radius_m, categories):
        return PlacesResult(places=places, sources_used=["overpass"], fetched_at=datetime.now(timezone.utc))

    async def fake_weather(lat, lon, target_date):
        return WeatherResult(
            latitude=lat, longitude=lon, fetched_at=datetime.now(timezone.utc),
            hourly=[HourlyWeather(time=WHEN, temperature_c=29.0, precipitation_probability_pct=20.0)],
        )

    monkeypatch.setattr(places_service, "search_places", fake_search)
    monkeypatch.setattr(weather_service, "get_hourly_weather", fake_weather)

    result = await trip_planner.plan_nearby_visit(ORIGIN, ["restaurant"], max_distance_m=1000, when=WHEN)

    assert {item.place.name for item in result.matched} == {"Gan va mo"}
    assert {item.place.name for item in result.unknown_opening_hours} == {"Gan nhung khong ro gio"}
    assert result.excluded_too_far == 1
    assert result.excluded_closed == 1
    assert "29.0" in result.weather_note
    assert result.error is None


@pytest.mark.asyncio
async def test_plan_nearby_visit_returns_error_when_places_fail(monkeypatch):
    async def failing_search(lat, lon, radius_m, categories):
        return PlacesResult(places=[], sources_used=[], fetched_at=datetime.now(timezone.utc), error="Overpass loi")

    monkeypatch.setattr(places_service, "search_places", failing_search)

    result = await trip_planner.plan_nearby_visit(ORIGIN, ["restaurant"], max_distance_m=1000, when=WHEN)

    assert result.error == "Overpass loi"
    assert result.matched == []
    assert result.unknown_opening_hours == []


@pytest.mark.asyncio
async def test_plan_nearby_visit_notes_missing_weather(monkeypatch):
    async def fake_search(lat, lon, radius_m, categories):
        return PlacesResult(places=[_place("A", 100, "24/7", True)], sources_used=["overpass"], fetched_at=datetime.now(timezone.utc))

    async def failing_weather(lat, lon, target_date):
        return WeatherResult(latitude=lat, longitude=lon, fetched_at=datetime.now(timezone.utc), hourly=[], error="Open-Meteo loi")

    monkeypatch.setattr(places_service, "search_places", fake_search)
    monkeypatch.setattr(weather_service, "get_hourly_weather", failing_weather)

    result = await trip_planner.plan_nearby_visit(ORIGIN, ["restaurant"], max_distance_m=1000, when=WHEN)

    assert "Không có dữ liệu thời tiết" in result.weather_note
    assert len(result.matched) == 1
