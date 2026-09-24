from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from typing import Literal

TravelMode = Literal["car", "motorcycle", "walking"]


class RouteResult(BaseModel):
    origin: tuple[float, float]
    destination: tuple[float, float]
    depart_at: datetime
    travel_mode: TravelMode = "car"
    distance_m: float | None = None
    # Thời gian di chuyển trả về: có traffic thật nếu source="tomtom", ước lượng tĩnh nếu "osrm".
    duration_s: float | None = None
    traffic_aware: bool = False
    source: str = "tomtom"
    fetched_at: datetime
    error: str | None = None
    # Route shape as [latitude, longitude] pairs for map rendering.
    geometry: list[tuple[float, float]] | None = None
    # Ghi chú hệ số điều chỉnh theo thời tiết/giờ cao điểm (traffic_heuristic), None nếu chưa áp dụng.
    heuristic_note: str | None = None
