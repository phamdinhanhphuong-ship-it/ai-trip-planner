from __future__ import annotations

from datetime import datetime, timezone

from app.models.routing import RouteResult
from app.services.traffic_heuristic import (
    RAIN_FACTOR,
    RUSH_HOUR_FACTOR,
    apply_weather_traffic_adjustment,
    travel_minutes,
)
from datetime import time as dt_time


def test_travel_minutes_no_adjustment_outside_rush_hour_no_rain():
    assert travel_minutes(20.0, dt_time(11, 0), rain_mm_h=0.0) == 20.0


def test_travel_minutes_rush_hour_factor():
    assert travel_minutes(20.0, dt_time(8, 0), rain_mm_h=0.0) == 20.0 * RUSH_HOUR_FACTOR


def test_travel_minutes_rain_factor():
    assert travel_minutes(20.0, dt_time(11, 0), rain_mm_h=3.0) == 20.0 * RAIN_FACTOR


def test_travel_minutes_combined_factors():
    expected = 20.0 * RUSH_HOUR_FACTOR * RAIN_FACTOR
    assert travel_minutes(20.0, dt_time(17, 0), rain_mm_h=5.0) == expected


def _route(traffic_aware: bool, source: str, depart_hour: int, duration_s: float = 1200.0) -> RouteResult:
    return RouteResult(
        origin=(10.77, 106.69),
        destination=(10.79, 106.72),
        depart_at=datetime(2026, 9, 19, depart_hour, 0, tzinfo=timezone.utc),
        distance_m=5000,
        duration_s=duration_s,
        traffic_aware=traffic_aware,
        source=source,
        fetched_at=datetime.now(timezone.utc),
    )


def test_osrm_static_route_gets_full_heuristic_during_rush_hour_and_rain():
    route = _route(traffic_aware=False, source="osrm", depart_hour=17)
    adjusted = apply_weather_traffic_adjustment(route, rain_mm_h=3.0)
    expected_factor = RUSH_HOUR_FACTOR * RAIN_FACTOR
    assert adjusted.duration_s == route.duration_s * expected_factor
    assert adjusted.heuristic_note is not None


def test_tomtom_real_traffic_only_gets_rain_factor_not_rush_hour():
    route = _route(traffic_aware=True, source="tomtom", depart_hour=17)
    adjusted = apply_weather_traffic_adjustment(route, rain_mm_h=3.0)
    assert adjusted.duration_s == route.duration_s * RAIN_FACTOR


def test_tomtom_real_traffic_no_rain_unchanged():
    route = _route(traffic_aware=True, source="tomtom", depart_hour=17)
    adjusted = apply_weather_traffic_adjustment(route, rain_mm_h=0.0)
    assert adjusted.duration_s == route.duration_s


def test_no_duration_returns_route_unchanged():
    route = _route(traffic_aware=True, source="tomtom", depart_hour=17, duration_s=None)  # type: ignore[arg-type]
    adjusted = apply_weather_traffic_adjustment(route, rain_mm_h=5.0)
    assert adjusted is route
