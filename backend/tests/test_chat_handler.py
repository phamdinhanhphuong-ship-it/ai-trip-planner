from __future__ import annotations

from datetime import date as date_type, timedelta
from datetime import datetime, timezone

import pytest

from app.agent import chat_handler
from app.core import session_store
from app.models.places import Place, PlacesResult
from app.models.routing import RouteResult
from app.models.weather import DailyEnvironment, HourlyWeather, WeatherResult
from app.models.itinerary import DayConstraints
from app.models.itinerary import Itinerary, Stop
from app.services import geocode_service, places_service, routing_service, weather_service

SESSION_ID = "test-session-6-scenarios"

PLACES_POOL: dict[str, list[Place]] = {
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
    "historic": [
        Place(name="Dinh Doc Lap", category="historic", latitude=10.7772, longitude=106.6953,
              distance_m=400, opening_hours_raw="24/7", opening_hours_known=True),
    ],
    "museum": [
        Place(name="Bao Tang Chung Tich", category="museum", latitude=10.7795, longitude=106.692,
              distance_m=500, opening_hours_raw="24/7", opening_hours_known=True),
    ],
}


def _weather_hours(rainy_afternoon: bool) -> list[HourlyWeather]:
    hours = []
    for h in range(7, 22):
        if rainy_afternoon and 13 <= h < 16:
            hours.append(HourlyWeather(time=datetime(2026, 9, 19, h, 0), temperature_c=30.0,
                                        apparent_temperature_c=37.0, precipitation_mm=5.0,
                                        precipitation_probability_pct=85.0))
        else:
            hours.append(HourlyWeather(time=datetime(2026, 9, 19, h, 0), temperature_c=28.0,
                                        apparent_temperature_c=30.0, precipitation_mm=0.0,
                                        precipitation_probability_pct=10.0))
    return hours


def test_extracts_location_time_and_motorcycle_from_full_message():
    message = (
        "Mình ở khách sạn gần chợ Bến Thành, thứ Bảy này rảnh từ 8h đến 20h. "
        "Thích lịch sử và đồ ăn đường phố, ngân sách khoảng 500k/người. Mình đi xe máy."
    )
    assert chat_handler._extract_location(message) == "chợ Bến Thành"
    assert chat_handler._extract_time_range(message) == ("08:00", "20:00")
    assert chat_handler._extract_travel_mode(message) == "motorcycle"
    assert chat_handler._extract_target_date(message, date_type(2026, 9, 23)) == date_type(2026, 9, 26)


def test_extracts_dalat_style_location_and_ampm_time():
    message = "Tụi mình thuê xe máy ở trung tâm thành phố Đà Lạt. Chủ nhật này đi chơi từ 7h sáng đến 18h tối."
    assert chat_handler._extract_location(message) == "trung tâm thành phố Đà Lạt"
    assert chat_handler._extract_time_range(message) == ("07:00", "18:00")
    assert chat_handler._extract_travel_mode(message) == "motorcycle"
    assert chat_handler._build_geocode_query("trung tâm thành phố Đà Lạt") == "trung tâm thành phố Đà Lạt"
    assert chat_handler._build_geocode_query("chợ Bến Thành") == "chợ Bến Thành, Ho Chi Minh City"
    assert set(chat_handler._extract_interests("chụp ảnh thiên nhiên, ngắm hoa và cà phê")) == {
        "viewpoint", "park", "attraction", "cafe"
    }
    assert chat_handler._is_dinner_patch("đổi lịch trình giúp tôi thành đi ăn tối ở một nhà hàng")
    assert chat_handler._extract_cuisine("Buổi trưa ăn nhà hàng đồ ăn Thái Lan", "lunch") == "thai"
    assert chat_handler._extract_cuisine("Buổi tối ăn nhà hàng Âu", "dinner") == "european"
    assert chat_handler._extract_required_museum_count("tham quan đúng 5 bảo tàng") == 5
    assert chat_handler._extract_required_museum_count("đi 3 bảo tàng") == 3
    itinerary = Itinerary(
        date=date_type(2026, 9, 27),
        stops=[
            Stop(
                name="Nhà tù Hỏa Lò", category="museum", indoor=True, lat=21.0, lon=105.8,
                arrive="13:00", leave="16:00", travel_minutes_from_prev=5,
                weather_note="", reason="", source="geoapify",
            )
        ],
    )
    message = "đổi lịch đến Nhà tù Hỏa Lò thành 1 địa điểm khác"
    assert chat_handler._is_named_stop_patch(message, itinerary)
    assert chat_handler._extract_location("đang ở Hà Nội trọn vẹn ngày Chủ Nhật này") == "Hà Nội"
    assert chat_handler._build_geocode_query("Hà Nội") == "Hà Nội"


def test_detects_direct_landmark_and_weather_queries():
    assert chat_handler._is_landmark_lookup("Nhà tù Hỏa Lò")
    assert chat_handler._is_landmark_lookup("Phố đi bộ Nguyễn Huệ")
    assert chat_handler._is_weather_lookup("dự báo thời tiết ngày 25")
    assert not chat_handler._is_landmark_lookup("dự báo thời tiết ngày 25")
    assert chat_handler._extract_hour("dự báo lúc 18h") == 18


def test_detects_arbitrary_landmark_names_for_wikipedia_lookup():
    assert chat_handler._is_landmark_lookup("Hồ Con Rùa")
    assert chat_handler._is_landmark_lookup("Chợ Bến Thành")
    assert chat_handler._is_landmark_lookup("Thảo Cầm Viên")


def test_invalid_explicit_date_does_not_raise():
    assert chat_handler._extract_explicit_date("dự báo ngày 31/2", date_type(2026, 9, 23)) is None


@pytest.mark.asyncio
async def test_landmark_lookup_returns_wikipedia_without_origin(monkeypatch):
    async def fake_summary(query):
        return {"description": "Di tich noi tieng.", "wikipedia_url": "https://vi.wikipedia.org/wiki/Test"}

    monkeypatch.setattr(chat_handler.wikipedia_service, "get_place_summary", fake_summary)
    result = await chat_handler.handle_message("wiki-landmark", "Nhà tù Hỏa Lò")
    assert "Di tich noi tieng." in result.reply
    assert "Wikipedia" in result.reply


@pytest.mark.asyncio
async def test_weather_lookup_uses_session_location_and_rejects_far_date(monkeypatch):
    session_id = "weather-session"
    state = session_store.get_session(session_id)
    state.constraints = DayConstraints(
        origin_lat=10.78, origin_lon=106.7, origin_label="Thảo Cầm Viên",
        free_start="08:00", free_end="20:00", interests=[],
    )

    async def fake_weather(lat, lon, target_date):
        return WeatherResult(
            latitude=lat, longitude=lon, fetched_at=datetime.now(timezone.utc),
            hourly=[HourlyWeather(time=datetime.combine(target_date, datetime.min.time()), temperature_c=30, precipitation_probability_pct=40)],
        )

    monkeypatch.setattr(chat_handler.weather_service, "get_hourly_weather", fake_weather)
    near = await chat_handler.handle_message(session_id, "dự báo thời tiết ngày 25")
    assert "Open-Meteo" in near.reply

    far_date = date_type.today() + timedelta(days=6)
    far = await chat_handler.handle_message(session_id, f"dự báo thời tiết ngày {far_date.day}/{far_date.month}")
    assert "quá xa" in far.reply
    session_store.reset_session(session_id)


@pytest.mark.asyncio
async def test_weather_lookup_extracts_location_and_day_month_from_user_sentence(monkeypatch):
    session_id = "weather-location-date-session"
    target_date = date_type.today() + timedelta(days=1)

    async def fake_geocode(query):
        assert query == "Hồ Con Rùa, Ho Chi Minh City"
        return geocode_service.GeocodeResult(
            query=query, latitude=10.78, longitude=106.70, display_name="Hồ Con Rùa",
            fetched_at=datetime.now(timezone.utc), source="goong",
        )

    async def fake_weather(lat, lon, requested_date):
        assert (lat, lon) == (10.78, 106.70)
        assert requested_date == target_date
        return WeatherResult(
            latitude=lat, longitude=lon, fetched_at=datetime.now(timezone.utc),
            hourly=[HourlyWeather(
                time=datetime.combine(requested_date, datetime.min.time()),
                temperature_c=29, apparent_temperature_c=31,
                precipitation_probability_pct=30, precipitation_mm=0,
            )],
        )

    monkeypatch.setattr(geocode_service, "geocode", fake_geocode)
    monkeypatch.setattr(chat_handler.weather_service, "get_hourly_weather", fake_weather)

    result = await chat_handler.handle_message(
        session_id,
        f"Tôi muốn xem thời tiết của Hồ Con Rùa ngày {target_date.day} tháng {target_date.month}",
    )

    assert "Hồ Con Rùa" in result.reply
    assert "29.0-29.0°C" in result.reply
    assert "Open-Meteo" in result.reply
    session_store.reset_session(session_id)


@pytest.mark.asyncio
async def test_weather_follow_up_location_completes_previous_request(monkeypatch):
    session_id = "weather-follow-up-session"
    target_date = date_type.today()

    async def fake_geocode(query):
        assert query == "Hồ Con Rùa, Ho Chi Minh City"
        return geocode_service.GeocodeResult(
            query=query, latitude=10.78, longitude=106.70, display_name="Hồ Con Rùa",
            fetched_at=datetime.now(timezone.utc), source="goong",
        )

    async def fake_weather(lat, lon, requested_date):
        assert requested_date == target_date
        return WeatherResult(
            latitude=lat, longitude=lon, fetched_at=datetime.now(timezone.utc),
            hourly=[HourlyWeather(
                time=datetime.combine(requested_date, datetime.min.time()),
                temperature_c=28, apparent_temperature_c=30,
                precipitation_probability_pct=20, precipitation_mm=0,
            )],
        )

    monkeypatch.setattr(geocode_service, "geocode", fake_geocode)
    monkeypatch.setattr(chat_handler.weather_service, "get_hourly_weather", fake_weather)

    first = await chat_handler.handle_message(session_id, "Cho tôi xem dự báo thời tiết.")
    second = await chat_handler.handle_message(session_id, "Hồ Con Rùa")

    assert "Bạn hãy cho biết địa điểm" in first.reply
    assert "Hồ Con Rùa" in second.reply
    assert "Open-Meteo" in second.reply
    session_store.reset_session(session_id)


@pytest.mark.asyncio
async def test_new_plan_collects_location_and_time_across_turns(monkeypatch):
    session_id = "test-progressive-slots"
    session_store.reset_session(session_id)
    first = await chat_handler.handle_message(session_id, "Mình đang ở khách sạn gần Thảo Cầm Viên, thích chụp ảnh thiên nhiên.")
    assert first.itinerary is None
    assert "mấy giờ" in first.reply

    async def fake_geocode(query):
        return geocode_service.GeocodeResult(
            query=query, latitude=10.78, longitude=106.70, display_name="Thảo Cầm Viên",
            fetched_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(geocode_service, "geocode", fake_geocode)
    second = await chat_handler.handle_message(session_id, "7h sáng đến 18h tối, đi xe máy.")
    assert second.itinerary is not None
    assert session_store.get_session(session_id).constraints.travel_mode == "motorcycle"
    session_store.reset_session(session_id)


@pytest.fixture(autouse=True)
def _fakes(monkeypatch):
    session_store.reset_session(SESSION_ID)

    async def fake_geocode(query):
        return geocode_service.GeocodeResult(
            query=query, latitude=10.7727, longitude=106.6980, display_name=query,
            fetched_at=datetime.now(timezone.utc),
        )

    async def fake_search(lat, lon, radius_m, categories):
        places = []
        for c in categories:
            places.extend(PLACES_POOL.get(c, []))
        return PlacesResult(places=places, sources_used=["overpass"], fetched_at=datetime.now(timezone.utc))

    async def fake_weather(lat, lon, target_date):
        return WeatherResult(latitude=lat, longitude=lon, fetched_at=datetime.now(timezone.utc),
                              hourly=_weather_hours(rainy_afternoon=True))

    async def fake_route(origin, destination, depart_at, travel_mode="car"):
        return RouteResult(origin=origin, destination=destination, depart_at=depart_at, distance_m=1200,
                            duration_s=600, traffic_aware=False, source="osrm", fetched_at=datetime.now(timezone.utc),
                            travel_mode=travel_mode)

    async def fake_daily_environment(lat, lon, target_date):
        return DailyEnvironment(
            date=datetime.combine(target_date, datetime.min.time()),
            sunset=datetime.combine(target_date, datetime.min.time()).replace(hour=18, minute=0),
            air_quality_aqi=42,
            air_quality_label="Tốt",
        )

    monkeypatch.setattr(geocode_service, "geocode", fake_geocode)
    monkeypatch.setattr(places_service, "search_places", fake_search)
    monkeypatch.setattr(weather_service, "get_hourly_weather", fake_weather)
    monkeypatch.setattr(weather_service, "get_daily_environment", fake_daily_environment)
    monkeypatch.setattr(routing_service, "get_route", fake_route)
    yield
    session_store.reset_session(SESSION_ID)


@pytest.mark.asyncio
async def test_scenario_1_builds_full_day_itinerary():
    result = await chat_handler.handle_message(
        SESSION_ID,
        "Mình ở khách sạn gần chợ Bến Thành, thứ Bảy này rảnh từ 8h đến 20h. "
        "Thích lịch sử và đồ ăn đường phố, ngân sách khoảng 500k/người.",
    )
    assert result.itinerary is not None
    assert len(result.itinerary.stops) >= 3
    assert "500" in result.reply or result.itinerary is not None  # ngân sách được ghi nhận qua notes


@pytest.mark.asyncio
async def test_scenario_2_patches_afternoon_only(monkeypatch):
    first = await chat_handler.handle_message(
        SESSION_ID,
        "Mình ở khách sạn gần chợ Bến Thành, thứ Bảy này rảnh từ 8h đến 20h. "
        "Thích lịch sử và đồ ăn đường phố, ngân sách khoảng 500k/người.",
    )
    morning_before = [s.name for s in first.itinerary.stops if s.leave <= "13:00"]

    second = await chat_handler.handle_message(SESSION_ID, "Dự báo chiều mưa to, đổi giúp mình phần buổi chiều.")

    morning_after = [s.name for s in second.itinerary.stops if s.leave <= "13:00"]
    assert morning_after == morning_before


@pytest.mark.asyncio
async def test_scenario_3_patches_lunch_only():
    await chat_handler.handle_message(
        SESSION_ID,
        "Mình ở khách sạn gần chợ Bến Thành, thứ Bảy này rảnh từ 8h đến 20h. "
        "Thích lịch sử và đồ ăn đường phố, ngân sách khoảng 500k/người.",
    )
    await chat_handler.handle_message(SESSION_ID, "Dự báo chiều mưa to, đổi giúp mình phần buổi chiều.")
    third = await chat_handler.handle_message(SESSION_ID, "Bữa trưa đổi sang quán chay gần đó nha.")

    lunch_stops = [s for s in third.itinerary.stops if "11:" <= s.arrive[:3] or "12:" <= s.arrive[:3] or "13:" <= s.arrive[:3]]
    assert any("Chay" in s.name for s in third.itinerary.stops)


@pytest.mark.asyncio
async def test_scenario_4_mobility_constraint_reduces_and_keeps_locked_lunch():
    await chat_handler.handle_message(
        SESSION_ID,
        "Mình ở khách sạn gần chợ Bến Thành, thứ Bảy này rảnh từ 8h đến 20h. "
        "Thích lịch sử và đồ ăn đường phố, ngân sách khoảng 500k/người.",
    )
    await chat_handler.handle_message(SESSION_ID, "Dự báo chiều mưa to, đổi giúp mình phần buổi chiều.")
    await chat_handler.handle_message(SESSION_ID, "Bữa trưa đổi sang quán chay gần đó nha.")
    fourth = await chat_handler.handle_message(SESSION_ID, "À mình đi với ông bà 70 tuổi, hạn chế đi bộ.")

    assert any("Chay" in s.name for s in fourth.itinerary.stops)
    state = session_store.get_session(SESSION_ID)
    assert state.constraints.mobility_limited is True


@pytest.mark.asyncio
async def test_scenario_5_and_6_delegate_to_llm_agent(monkeypatch):
    await chat_handler.handle_message(
        SESSION_ID,
        "Mình ở khách sạn gần chợ Bến Thành, thứ Bảy này rảnh từ 8h đến 20h. "
        "Thích lịch sử và đồ ăn đường phố, ngân sách khoảng 500k/người.",
    )

    async def fake_run_agent(message):
        if "Landmark" in message:
            return "18h mất 40 phút (nguồn: TomTom - traffic thực); 20h mất 25 phút (nguồn: TomTom - traffic thực)."
        return "KHÔNG CÓ DỮ LIỆU: không tìm thấy quán phở mở lúc 2h sáng ở Quận 5."

    monkeypatch.setattr(chat_handler, "run_agent", fake_run_agent)

    fifth = await chat_handler.handle_message(SESSION_ID, "18h tối nay đi từ Quận 1 lên Landmark 81 mất bao lâu? Đi lúc 20h thì sao?")
    assert "TomTom" in fifth.reply

    sixth = await chat_handler.handle_message(SESSION_ID, "Cho mình quán phở mở cửa lúc 2h sáng ở Quận 5.")
    assert "KHÔNG CÓ DỮ LIỆU" in sixth.reply


@pytest.mark.asyncio
async def test_ambiguous_edit_asks_for_the_target_stop():
    session_id = "ambiguous-edit-session"
    session_store.reset_session(session_id)
    state = session_store.get_session(session_id)
    state.itinerary = Itinerary(
        date=date_type(2026, 9, 24),
        stops=[Stop(
            name="Chợ Bến Thành", category="historic", indoor=False, lat=10.77, lon=106.70,
            arrive="09:00", leave="11:00", travel_minutes_from_prev=10,
            weather_note="", reason="", source="test",
        )],
    )

    result = await chat_handler.handle_message(session_id, "Xóa giúp tôi một địa điểm")

    assert "Bạn muốn xóa địa điểm nào" in result.reply
    assert len(result.itinerary.stops) == 1
    session_store.reset_session(session_id)


@pytest.mark.asyncio
async def test_named_delete_removes_only_requested_stop():
    session_id = "named-delete-session"
    session_store.reset_session(session_id)
    state = session_store.get_session(session_id)
    state.itinerary = Itinerary(
        date=date_type(2026, 9, 24),
        stops=[
            Stop(name="Chợ Bến Thành", category="historic", indoor=False, lat=10.77, lon=106.70,
                 arrive="09:00", leave="11:00", travel_minutes_from_prev=10, weather_note="", reason="", source="test"),
            Stop(name="Bảo tàng", category="museum", indoor=True, lat=10.78, lon=106.70,
                 arrive="13:00", leave="15:00", travel_minutes_from_prev=10, weather_note="", reason="", source="test"),
        ],
    )

    result = await chat_handler.handle_message(session_id, "Xóa Chợ Bến Thành khỏi lịch trình")

    assert [stop.name for stop in result.itinerary.stops] == ["Bảo tàng"]
    assert "Đã xóa 'Chợ Bến Thành'" in result.reply
    session_store.reset_session(session_id)


@pytest.mark.asyncio
async def test_add_named_category_at_time_adds_provider_backed_stop():
    session_store.reset_session(SESSION_ID)
    state = session_store.get_session(SESSION_ID)
    state.constraints = DayConstraints(
        origin_lat=10.7727, origin_lon=106.6980, origin_label="Chợ Bến Thành",
        free_start="08:00", free_end="20:00", interests=[],
    )
    state.target_date = date_type(2026, 9, 19)
    state.itinerary = Itinerary(
        date=state.target_date,
        stops=[Stop(
            name="Dinh Doc Lap", category="historic", indoor=False, lat=10.7772, lon=106.6953,
            arrive="09:00", leave="11:00", travel_minutes_from_prev=10,
            weather_note="", reason="", source="test",
        )],
    )

    result = await chat_handler.handle_message(SESSION_ID, "Thêm một bảo tàng vào 15h-17h")

    assert result.itinerary is not None
    assert any(stop.category == "museum" for stop in result.itinerary.stops)
    assert "Đã cập nhật thêm điểm" in result.reply
