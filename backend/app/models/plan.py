from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.models.places import Place


class PlanItem(BaseModel):
    place: Place
    # None = không rõ giờ mở cửa (không được coi là đang mở hay đóng).
    is_open: bool | None


class TripPlanResult(BaseModel):
    origin: tuple[float, float]
    when: datetime
    matched: list[PlanItem]
    unknown_opening_hours: list[PlanItem]
    excluded_too_far: int
    excluded_closed: int
    sources_used: list[str]
    weather_note: str | None = None
    error: str | None = None
