from datetime import date, datetime, timezone

import pytest

from app.agent import tools
from app.models.places import Place, PlacesResult
from app.models.routing import RouteResult
from app.models.weather import HourlyWeather, WeatherResult
from app.services import geocode_service, places_service, routing_service, trip_planner, weather_service


@pytest.mark.asyncio
async def test_geocode_place_tool_success(monkeypatch):
    async def fake_geocode(query):
        return geocode_service.GeocodeResult(
            query=query, latitude=10.77, longitude=106.70, display_name="Cho Ben Thanh",
                source="goong", fetched_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(geocode_service, "geocode", fake_geocode)

    out = await tools.geocode_place.ainvoke({"query": "Cho Ben Thanh"})
    assert "lat=10.77" in out
    assert "Goong" in out


@pytest.mark.asyncio
async def test_geocode_place_tool_no_data(monkeypatch):
    async def fake_geocode(query):
        return geocode_service.GeocodeResult(
            query=query, fetched_at=datetime.now(timezone.utc), error="khong tim thay"
        )

    monkeypatch.setattr(geocode_service, "geocode", fake_geocode)

    out = await tools.geocode_place.ainvoke({"query": "xyz khong ton tai"})
    assert out.startswith(tools.NO_DATA_PREFIX)


@pytest.mark.asyncio
async def test_get_weather_hourly_tool_success(monkeypatch):
    async def fake_weather(lat, lon, target_date):
        return WeatherResult(
            latitude=lat, longitude=lon, fetched_at=datetime.now(timezone.utc),
            hourly=[HourlyWeather(time=datetime(2026, 9, 21, 18, 0), temperature_c=28.0,
                                   precipitation_mm=0.0, precipitation_probability_pct=10.0,
                                   weather_code=1, wind_speed_kmh=10.0)],
        )

    monkeypatch.setattr(weather_service, "get_hourly_weather", fake_weather)

    out = await tools.get_weather_hourly.ainvoke({"lat": 10.77, "lon": 106.70, "date_str": "2026-09-21", "hour": 18})
    assert "28.0" in out
    assert "Open-Meteo" in out


@pytest.mark.asyncio
async def test_get_weather_hourly_tool_bad_date():
    out = await tools.get_weather_hourly.ainvoke({"lat": 10.77, "lon": 106.70, "date_str": "not-a-date", "hour": 18})
    assert out.startswith(tools.NO_DATA_PREFIX)


@pytest.mark.asyncio
async def test_get_weather_hourly_tool_rejects_invalid_hour():
    out = await tools.get_weather_hourly.ainvoke({"lat": 10.77, "lon": 106.70, "date_str": "2026-09-21", "hour": 24})
    assert "hour phải nằm trong khoảng 0-23" in out


@pytest.mark.asyncio
async def test_search_nearby_places_tool_separates_unknown_hours(monkeypatch):
    async def fake_search(lat, lon, radius_m, categories):
        return PlacesResult(
            places=[
                Place(name="Quan A", category="restaurant", latitude=lat, longitude=lon,
                      distance_m=100, opening_hours_raw="08:00-22:00", opening_hours_known=True),
                Place(name="Quan B", category="restaurant", latitude=lat, longitude=lon,
                      distance_m=200, opening_hours_known=False),
            ],
            sources_used=["overpass"],
            fetched_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(places_service, "search_places", fake_search)

    out = await tools.search_nearby_places.ainvoke(
        {"lat": 10.77, "lon": 106.70, "radius_m": 1000, "categories_csv": "restaurant", "depart_hour": 18}
    )
    assert "Quan A" in out
    assert "KHÔNG RÕ giờ mở cửa" in out
    assert "Quan B" in out


@pytest.mark.asyncio
async def test_get_route_between_tool_formats_source_label(monkeypatch):
    async def fake_route(origin, destination, depart_at):
        return RouteResult(
            origin=origin, destination=destination, depart_at=depart_at,
            distance_m=5000, duration_s=600, traffic_aware=True, source="tomtom",
            fetched_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(routing_service, "get_route", fake_route)

    out = await tools.get_route_between.ainvoke(
        {"origin_lat": 10.77, "origin_lon": 106.70, "dest_lat": 10.79, "dest_lon": 106.72,
         "depart_at_iso": "2026-09-21T18:00:00"}
    )
    assert "5.0km" in out
    assert "TomTom" in out


@pytest.mark.asyncio
async def test_compare_route_departure_times_tool_shows_warning(monkeypatch):
    async def fake_compare(origin, destination, depart_times):
        return [
            RouteResult(origin=origin, destination=destination, depart_at=depart_times[0],
                        distance_m=5000, duration_s=600, traffic_aware=True, source="tomtom",
                        fetched_at=datetime.now(timezone.utc), error="[Cảnh báo: nguồn khác nhau]"),
            RouteResult(origin=origin, destination=destination, depart_at=depart_times[1],
                        distance_m=5000, duration_s=900, traffic_aware=False, source="osrm",
                        fetched_at=datetime.now(timezone.utc), error="[Cảnh báo: nguồn khác nhau]"),
        ]

    monkeypatch.setattr(routing_service, "compare_departure_times", fake_compare)

    out = await tools.compare_route_departure_times.ainvoke(
        {"origin_lat": 10.77, "origin_lon": 106.70, "dest_lat": 10.79, "dest_lon": 106.72,
         "depart_times_csv": "2026-09-21T18:00:00,2026-09-21T20:00:00"}
    )
    assert "Cảnh báo" in out
    assert "TomTom" in out and "OSRM" in out


@pytest.mark.asyncio
async def test_plan_nearby_visit_tool_separates_matched_and_unknown(monkeypatch):
    from app.models.plan import PlanItem, TripPlanResult

    async def fake_plan(origin, categories, max_distance_m, when):
        return TripPlanResult(
            origin=origin, when=when,
            matched=[PlanItem(place=Place(name="Quan Mo", category="restaurant", latitude=10.78, longitude=106.70,
                                           distance_m=150, opening_hours_raw="08:00-22:00", opening_hours_known=True), is_open=True)],
            unknown_opening_hours=[PlanItem(place=Place(name="Quan Khong Ro", category="restaurant", latitude=10.78,
                                                         longitude=106.70, distance_m=250, opening_hours_known=False), is_open=None)],
            excluded_too_far=2, excluded_closed=1, sources_used=["overpass"],
            weather_note="28.0°C, xác suất mưa 10% (nguồn: Open-Meteo)",
        )

    monkeypatch.setattr(trip_planner, "plan_nearby_visit", fake_plan)

    out = await tools.plan_nearby_visit.ainvoke(
        {"lat": 10.77, "lon": 106.70, "categories_csv": "restaurant", "max_distance_m": 1000,
         "depart_at_iso": "2026-09-21T18:00:00"}
    )
    assert "Quan Mo" in out
    assert "KHÔNG RÕ giờ mở cửa" in out and "Quan Khong Ro" in out
    assert "loại 2" in out and "1 địa điểm đang đóng cửa" in out
    assert "Open-Meteo" in out
