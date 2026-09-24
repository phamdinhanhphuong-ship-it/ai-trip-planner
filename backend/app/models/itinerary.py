from __future__ import annotations

from datetime import date

from pydantic import BaseModel
from app.models.routing import TravelMode


class Stop(BaseModel):
    """Một điểm trong lịch trình — khớp đúng schema bắt buộc của đề bài."""

    name: str
    category: str
    indoor: bool
    lat: float
    lon: float
    arrive: str  # "HH:MM"
    leave: str  # "HH:MM"
    travel_minutes_from_prev: float
    wait_minutes_from_prev: float = 0
    weather_note: str
    reason: str
    source: str
    meal_type: str | None = None
    route_geometry: list[tuple[float, float]] | None = None
    wikipedia_url: str | None = None
    wikipedia_description: str | None = None
    route_source: str | None = None
    traffic_aware: bool = False
    # Đánh dấu điểm này vừa bị sửa ở lượt hội thoại hiện tại (để UI highlight, không phải field theo đề).
    just_changed: bool = False


class Itinerary(BaseModel):
    date: date
    stops: list[Stop]
    # Số khung giờ không tìm được địa điểm thật phù hợp (bỏ trống thay vì bịa).
    unfilled_slots: int = 0
    notes: list[str] = []
    budget_vnd_per_person: int | None = None
    estimated_cost_vnd: int | None = None
    sunset: str | None = None
    air_quality_aqi: int | None = None
    air_quality_label: str | None = None
    daily_info_source: str | None = None


class DayConstraints(BaseModel):
    """Ràng buộc hiện tại của 1 session — được cập nhật dần qua các lượt hội thoại."""

    origin_lat: float
    origin_lon: float
    origin_label: str
    free_start: str  # "HH:MM"
    free_end: str  # "HH:MM"
    interests: list[str]
    budget_vnd_per_person: int | None = None
    travel_mode: TravelMode = "car"
    required_museum_count: int = 0
    lunch_cuisine: str | None = None
    dinner_cuisine: str | None = None
    mobility_limited: bool = False


class ChatTurnResult(BaseModel):
    session_id: str
    reply: str
    itinerary: Itinerary | None = None
