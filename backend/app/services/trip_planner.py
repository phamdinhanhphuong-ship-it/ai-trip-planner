from __future__ import annotations

from datetime import datetime

from app.models.plan import PlanItem, TripPlanResult
from app.services import places_service, weather_service


async def plan_nearby_visit(
    origin: tuple[float, float],
    categories: list[str],
    max_distance_m: float,
    when: datetime,
    search_radius_m: int | None = None,
) -> TripPlanResult:
    """Lọc địa điểm quanh `origin` theo sở thích (categories) + khoảng cách + giờ mở cửa tại `when`.

    Đây là tầng lọc quyết định (deterministic), không phụ thuộc vào việc LLM có nhớ áp dụng
    đúng bộ lọc hay không — đảm bảo luôn tuân thủ 3 yêu cầu lọc bắt buộc của đề bài.
    Địa điểm biết chắc đang đóng cửa bị loại; địa điểm không rõ giờ mở cửa được giữ lại
    riêng (unknown_opening_hours) để không bịa ra kết luận "đang mở"/"đang đóng".
    """
    radius = search_radius_m or int(max_distance_m)
    places_result = await places_service.search_places(origin[0], origin[1], radius, categories)

    if places_result.error and not places_result.places:
        return TripPlanResult(
            origin=origin,
            when=when,
            matched=[],
            unknown_opening_hours=[],
            excluded_too_far=0,
            excluded_closed=0,
            sources_used=places_result.sources_used,
            error=places_result.error,
        )

    within_distance = places_service.filter_by_distance(places_result.places, max_distance_m)
    excluded_too_far = len(places_result.places) - len(within_distance)

    open_places, unknown_places = places_service.filter_open_at(within_distance, when)
    excluded_closed = len(within_distance) - len(open_places) - len(unknown_places)

    weather_result = await weather_service.get_hourly_weather(origin[0], origin[1], when.date())
    weather_point = weather_service.get_weather_at_hour(weather_result, when)
    if weather_result.error:
        weather_note = f"Không có dữ liệu thời tiết: {weather_result.error}"
    elif weather_point:
        weather_note = (
            f"{weather_point.temperature_c}°C, xác suất mưa {weather_point.precipitation_probability_pct}% "
            "(nguồn: Open-Meteo)"
        )
    else:
        weather_note = "Không có dữ liệu thời tiết cho đúng khung giờ này"

    return TripPlanResult(
        origin=origin,
        when=when,
        matched=[PlanItem(place=p, is_open=True) for p in open_places],
        unknown_opening_hours=[PlanItem(place=p, is_open=None) for p in unknown_places],
        excluded_too_far=excluded_too_far,
        excluded_closed=excluded_closed,
        sources_used=places_result.sources_used,
        weather_note=weather_note,
    )
