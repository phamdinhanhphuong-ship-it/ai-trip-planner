from __future__ import annotations

from datetime import time

from app.models.routing import RouteResult

# Heuristic mẫu theo đề bài — dùng khi KHÔNG có traffic thật (nguồn "osrm"),
# vì traffic thật (TomTom, traffic_aware=True) đã tự phản ánh giờ cao điểm rồi.
RUSH_HOURS: list[tuple[time, time]] = [(time(7, 0), time(9, 0)), (time(16, 30), time(19, 0))]
RUSH_HOUR_FACTOR = 1.6
RAIN_FACTOR = 1.3
RAIN_THRESHOLD_MM_H = 2.0


def travel_minutes(base_min: float, depart: time, rain_mm_h: float) -> float:
    """Công thức ước lượng thời gian di chuyển mẫu trong đề bài (fallback khi không có traffic thật)."""
    factor = 1.0
    if any(start <= depart < end for start, end in RUSH_HOURS):
        factor *= RUSH_HOUR_FACTOR
    if rain_mm_h >= RAIN_THRESHOLD_MM_H:
        factor *= RAIN_FACTOR
    return base_min * factor


def _is_rush_hour(depart: time) -> bool:
    return any(start <= depart < end for start, end in RUSH_HOURS)


def describe_adjustment(depart: time, rain_mm_h: float, traffic_aware: bool) -> str:
    reasons = []
    if not traffic_aware and _is_rush_hour(depart):
        reasons.append(f"giờ cao điểm x{RUSH_HOUR_FACTOR}")
    if rain_mm_h >= RAIN_THRESHOLD_MM_H:
        reasons.append(f"mưa {rain_mm_h}mm/h x{RAIN_FACTOR}")
    if not reasons:
        return "không cần điều chỉnh thêm (ngoài giờ cao điểm, không mưa đáng kể)"
    return "điều chỉnh ước lượng: " + " và ".join(reasons)


def apply_weather_traffic_adjustment(route: RouteResult, rain_mm_h: float) -> RouteResult:
    """Cộng thêm hệ số mưa (đề bài yêu cầu thời tiết luôn làm tăng thời gian di chuyển).

    - Nếu route đã traffic thật (TomTom, traffic_aware=True): giờ cao điểm đã được phản ánh,
      chỉ cộng thêm hệ số mưa.
    - Nếu route là ước lượng tĩnh (OSRM, traffic_aware=False): áp dụng đầy đủ công thức heuristic
      mẫu của đề bài (giờ cao điểm + mưa), và route.error đã có nhãn "ước lượng" từ routing_service.
    """
    if route.duration_s is None:
        return route

    depart_time = route.depart_at.time()
    base_min = route.duration_s / 60

    if route.traffic_aware:
        factor = RAIN_FACTOR if rain_mm_h >= RAIN_THRESHOLD_MM_H else 1.0
    else:
        adjusted = travel_minutes(base_min, depart_time, rain_mm_h)
        factor = (adjusted / base_min) if base_min else 1.0

    note = describe_adjustment(depart_time, rain_mm_h, route.traffic_aware)
    return route.model_copy(update={"duration_s": base_min * factor * 60, "heuristic_note": note})
