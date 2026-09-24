from __future__ import annotations

from datetime import date, datetime, time, timezone

import pytest

from app.models.itinerary import DayConstraints
from app.models.places import Place, PlacesResult
from app.models.routing import RouteResult
from app.models.weather import HourlyWeather, WeatherResult
from app.services import itinerary_builder, places_service, routing_service, weather_service

TARGET_DATE = date(2026, 9, 19)

PLACES_POOL: dict[str, list[Place]] = {
    # 4 lựa chọn gần hơn "Quan Chay An Lac" để 4 khung ăn mặc định (sáng/trưa/giải lao/tối) không
    # vô tình dùng hết quán chay trước khi bài test patch tìm nó bằng từ khóa "chay".
    "restaurant": [
        Place(name="Com Tam Yummy", category="restaurant", latitude=10.775, longitude=106.695,
              distance_m=300, opening_hours_raw="24/7", opening_hours_known=True),
        Place(name="Chao Long Chi Lan", category="restaurant", latitude=10.7745, longitude=106.6965,
              distance_m=450, opening_hours_raw="24/7", opening_hours_known=True),
        Place(name="Quan Chay An Lac", category="restaurant", latitude=10.774, longitude=106.696,
              distance_m=600, opening_hours_raw="24/7", opening_hours_known=True),
    ],
    "fast_food": [
        Place(name="Pho Hoa", category="fast_food", latitude=10.773, longitude=106.694,
              distance_m=250, opening_hours_raw="24/7", opening_hours_known=True),
        Place(name="Banh Mi Huynh Hoa", category="fast_food", latitude=10.7735, longitude=106.6945,
              distance_m=350, opening_hours_raw="24/7", opening_hours_known=True),
    ],
    "cafe": [
        Place(name="Cafe Trong Nha", category="cafe", latitude=10.776, longitude=106.697,
              distance_m=350, opening_hours_raw="24/7", opening_hours_known=True),
    ],
    "historic": [
        Place(name="Dinh Doc Lap", category="historic", latitude=10.7772, longitude=106.6953,
              distance_m=400, opening_hours_raw="24/7", opening_hours_known=True),
    ],
    "museum": [
        Place(name="Bao Tang Chung Tich", category="museum", latitude=10.7795, longitude=106.692,
              distance_m=500, opening_hours_raw="24/7", opening_hours_known=True,
              wikipedia_url="https://vi.wikipedia.org/wiki/B%E1%BA%A3o_t%C3%A0ng",
              description="Bảo tàng có bài viết trên Wikipedia."),
    ],
}


def _weather_hours(rainy_afternoon: bool) -> list[HourlyWeather]:
    hours = []
    for h in range(7, 22):
        if rainy_afternoon and 13 <= h < 16:
            hours.append(HourlyWeather(
                time=datetime(2026, 9, 19, h, 0), temperature_c=30.0, apparent_temperature_c=37.0,
                precipitation_mm=5.0, precipitation_probability_pct=85.0,
            ))
        else:
            hours.append(HourlyWeather(
                time=datetime(2026, 9, 19, h, 0), temperature_c=28.0, apparent_temperature_c=30.0,
                precipitation_mm=0.0, precipitation_probability_pct=10.0,
            ))
    return hours


def test_expand_museum_blocks_supports_variable_requested_counts():
    blocks = [
        itinerary_builder.TimeBlock("morning", time(9, 30), time(11, 0), "sight"),
        itinerary_builder.TimeBlock("lunch", time(11, 30), time(13, 0), "meal"),
        itinerary_builder.TimeBlock("afternoon", time(13, 0), time(16, 0), "sight"),
    ]

    expanded = itinerary_builder._expand_museum_blocks(blocks, 5)

    assert len([block for block in expanded if block.kind == "sight"]) == 5
    assert len([block for block in expanded if block.kind == "meal"]) == 1


def test_validate_meal_coverage_reports_missing_lunch_and_food_heavy_schedule():
    itinerary = itinerary_builder.Itinerary(
        date=TARGET_DATE,
        stops=[
            itinerary_builder.Stop(
                name="Breakfast", category="restaurant", indoor=True, lat=10.77, lon=106.70,
                arrive="07:30", leave="08:30", travel_minutes_from_prev=0,
                weather_note="", reason="", source="test", meal_type="breakfast",
            ),
            itinerary_builder.Stop(
                name="Cafe 1", category="cafe", indoor=True, lat=10.77, lon=106.70,
                arrive="10:00", leave="11:00", travel_minutes_from_prev=0,
                weather_note="", reason="", source="test", meal_type="snack",
            ),
            itinerary_builder.Stop(
                name="Dinner", category="restaurant", indoor=True, lat=10.77, lon=106.70,
                arrive="19:00", leave="20:00", travel_minutes_from_prev=0,
                weather_note="", reason="", source="test", meal_type="dinner",
            ),
        ],
    )

    notes = itinerary_builder.validate_meal_coverage(itinerary, "07:00", "21:00")

    assert any("bữa trưa" in note for note in notes)
    assert any("vượt tỷ lệ" in note for note in notes)


def _install_fakes(monkeypatch, rainy_afternoon: bool = True):
    async def fake_search(lat, lon, radius_m, categories):
        places = []
        for c in categories:
            places.extend(PLACES_POOL.get(c, []))
        return PlacesResult(places=places, sources_used=["overpass"], fetched_at=datetime.now(timezone.utc))

    async def fake_weather(lat, lon, target_date):
        return WeatherResult(
            latitude=lat, longitude=lon, fetched_at=datetime.now(timezone.utc),
            hourly=_weather_hours(rainy_afternoon),
        )

    async def fake_route(origin, destination, depart_at):
        return RouteResult(
            origin=origin, destination=destination, depart_at=depart_at,
            distance_m=1200, duration_s=600, traffic_aware=False, source="osrm",
            fetched_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(places_service, "search_places", fake_search)
    monkeypatch.setattr(weather_service, "get_hourly_weather", fake_weather)
    monkeypatch.setattr(routing_service, "get_route", fake_route)


def _constraints(**overrides) -> DayConstraints:
    base = dict(
        origin_lat=10.7727, origin_lon=106.6980, origin_label="Cho Ben Thanh",
        free_start="08:00", free_end="20:00", interests=["historic", "fast_food", "restaurant"],
    )
    base.update(overrides)
    return DayConstraints(**base)


@pytest.mark.asyncio
async def test_build_itinerary_full_day_no_overlap_and_indoor_during_rain(monkeypatch):
    _install_fakes(monkeypatch, rainy_afternoon=True)

    result = await itinerary_builder.build_itinerary(_constraints(), TARGET_DATE)

    assert len(result.stops) >= 3
    for prev, nxt in zip(result.stops, result.stops[1:]):
        assert prev.leave <= nxt.arrive  # không chồng giờ
    assert any(stop.wait_minutes_from_prev > 0 for stop in result.stops)

    afternoon_sight = next((s for s in result.stops if s.category in ("museum", "historic") and "13" <= s.arrive[:2] <= "16"), None)
    # Trời mưa 80-85% buổi chiều -> chỉ còn category trong nhà (museum) khả dụng cho slot chiều.
    if afternoon_sight is not None:
        assert afternoon_sight.indoor is True


@pytest.mark.asyncio
async def test_build_itinerary_tracks_budget_in_output(monkeypatch):
    _install_fakes(monkeypatch, rainy_afternoon=False)

    result = await itinerary_builder.build_itinerary(
        _constraints(budget_vnd_per_person=1_000_000), TARGET_DATE
    )

    assert result.budget_vnd_per_person == 1_000_000
    assert result.estimated_cost_vnd is None
    assert any("Ngân sách người dùng" in note for note in result.notes)


@pytest.mark.asyncio
async def test_build_itinerary_carries_wikipedia_to_landmark_stop(monkeypatch):
    _install_fakes(monkeypatch, rainy_afternoon=False)

    result = await itinerary_builder.build_itinerary(_constraints(interests=["museum"]), TARGET_DATE)

    museum_stop = next(stop for stop in result.stops if stop.category == "museum")
    assert museum_stop.wikipedia_url == "https://vi.wikipedia.org/wiki/B%E1%BA%A3o_t%C3%A0ng"
    assert museum_stop.wikipedia_description == "Bảo tàng có bài viết trên Wikipedia."


@pytest.mark.asyncio
async def test_build_itinerary_reports_unfilled_when_no_data(monkeypatch):
    async def empty_search(lat, lon, radius_m, categories):
        return PlacesResult(places=[], sources_used=[], fetched_at=datetime.now(timezone.utc))

    async def fake_weather(lat, lon, target_date):
        return WeatherResult(latitude=lat, longitude=lon, fetched_at=datetime.now(timezone.utc), hourly=_weather_hours(False))

    monkeypatch.setattr(places_service, "search_places", empty_search)
    monkeypatch.setattr(weather_service, "get_hourly_weather", fake_weather)

    result = await itinerary_builder.build_itinerary(_constraints(), TARGET_DATE)

    assert result.stops == []
    assert result.unfilled_slots > 0
    assert any("bỏ trống" in n for n in result.notes)


@pytest.mark.asyncio
async def test_patch_afternoon_keeps_morning_unchanged(monkeypatch):
    _install_fakes(monkeypatch, rainy_afternoon=False)
    original = await itinerary_builder.build_itinerary(_constraints(), TARGET_DATE)
    morning_before = [s for s in original.stops if s.leave <= "13:00"]

    _install_fakes(monkeypatch, rainy_afternoon=True)
    patched = await itinerary_builder.patch_afternoon(_constraints(), original, TARGET_DATE, cutoff=time(13, 0))

    morning_after = [s for s in patched.stops if s.leave <= "13:00"]
    assert [s.name for s in morning_after] == [s.name for s in morning_before]
    assert all(not s.just_changed for s in morning_after)


@pytest.mark.asyncio
async def test_patch_single_stop_replaces_only_lunch(monkeypatch):
    _install_fakes(monkeypatch, rainy_afternoon=False)
    original = await itinerary_builder.build_itinerary(_constraints(), TARGET_DATE)

    patched, error = await itinerary_builder.patch_single_stop(
        _constraints(), original, TARGET_DATE,
        match_categories=["restaurant", "fast_food"],
        time_window=(time(11, 0), time(14, 0)),
        name_keyword="chay",
    )

    assert error is None
    lunch_stops = [s for s in patched.stops if time(11, 0) <= time.fromisoformat(s.arrive + ":00") < time(14, 0)]
    assert any("Chay" in s.name for s in lunch_stops)
    other_names_before = {s.name for s in original.stops if not (time(11, 0) <= time.fromisoformat(s.arrive + ":00") < time(14, 0))}
    other_names_after = {s.name for s in patched.stops if not (time(11, 0) <= time.fromisoformat(s.arrive + ":00") < time(14, 0))}
    assert other_names_before == other_names_after


@pytest.mark.asyncio
async def test_patch_single_stop_no_match_returns_error_without_fabricating(monkeypatch):
    _install_fakes(monkeypatch, rainy_afternoon=False)
    original = await itinerary_builder.build_itinerary(_constraints(), TARGET_DATE)

    patched, error = await itinerary_builder.patch_single_stop(
        _constraints(), original, TARGET_DATE,
        match_categories=["restaurant", "fast_food"],
        time_window=(time(2, 0), time(3, 0)),
        name_keyword="pho dem khuya khong ton tai",
    )

    assert error is not None
    assert patched.stops == original.stops


@pytest.mark.asyncio
async def test_apply_mobility_constraint_uses_reduced_template_and_keeps_locked_stop(monkeypatch):
    _install_fakes(monkeypatch, rainy_afternoon=False)
    original = await itinerary_builder.build_itinerary(_constraints(), TARGET_DATE)
    patched, _ = await itinerary_builder.patch_single_stop(
        _constraints(), original, TARGET_DATE,
        match_categories=["restaurant", "fast_food"],
        time_window=(time(11, 0), time(14, 0)),
        name_keyword="chay",
    )

    limited_constraints = _constraints(mobility_limited=True)
    final = await itinerary_builder.apply_mobility_constraint(limited_constraints, patched, TARGET_DATE)

    assert any("Chay" in s.name for s in final.stops)
    assert len(final.stops) <= len(patched.stops)
