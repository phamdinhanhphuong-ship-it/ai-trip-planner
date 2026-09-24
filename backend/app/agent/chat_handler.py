from __future__ import annotations

import re
import logging
from datetime import date as date_type, datetime, timedelta
from datetime import time

from app.agent.graph import run_agent
from app.core.session_store import SessionState, get_session, get_session_lock
from app.models.itinerary import ChatTurnResult, DayConstraints, Itinerary
from app.services import geocode_service, itinerary_builder, weather_service, wikipedia_service

logger = logging.getLogger(__name__)

# Regex nhận diện đơn giản cho 4 kịch bản có state (đề bài yêu cầu patch cục bộ, không generate lại).
# Câu hỏi mở (so sánh giờ khởi hành, tra cứu địa điểm hiếm...) được chuyển cho LLM agent xử lý bằng tool.

INTEREST_KEYWORDS: dict[str, str] = {
    "lịch sử": "historic",
    "di tích": "historic",
    "bảo tàng": "museum",
    "đồ ăn đường phố": "fast_food",
    "ăn vặt": "fast_food",
    "quán ăn": "restaurant",
    "cà phê": "cafe",
    "công viên": "park",
    "chùa": "temple",
    "ngắm cảnh": "viewpoint",
    "ngắm hoa": "attraction",
    "chụp ảnh": "viewpoint",
    "thiên nhiên": "park",
    "tham quan": "attraction",
}

CUISINE_KEYWORDS = {
    "thái lan": "thai",
    "thái": "thai",
    "thai": "thai",
    "âu": "european",
    "european": "european",
    "ý": "italian",
    "nhật": "japanese",
    "hàn": "korean",
}

TIME_RANGE_RE = re.compile(
    r"(\d{1,2})\s*h(?:\s*(\d{2}))?\s*(sáng|chiều|tối|đêm)?\s*"
    r"(?:đến|tới|-|->)\s*"
    r"(\d{1,2})\s*h(?:\s*(\d{2}))?\s*(sáng|chiều|tối|đêm)?",
    re.IGNORECASE,
)
BUDGET_RE = re.compile(r"(\d[\d.,]{2,})\s*(k|nghìn|đồng|vnd)", re.IGNORECASE)
NEAR_RE = re.compile(r"gần\s+([^,\.]+)")
AT_RE = re.compile(r"(?:^|\s)(?:ở|tại)\s+([^,\.]+)")
FROM_RE = re.compile(r"(?:xuất phát|bắt đầu|đi)\s+từ\s+([^,;\.]+)", re.IGNORECASE)

VIETNAMESE_DIGITS = {
    "một": 1,
    "hai": 2,
    "ba": 3,
    "bốn": 4,
    "tư": 4,
    "năm": 5,
    "sáu": 6,
    "bảy": 7,
    "tám": 8,
    "chín": 9,
    "mười": 10,
}

LUNCH_WINDOW = (time(11, 0), time(14, 0))
DINNER_WINDOW = (time(18, 0), time(21, 0))


def _extract_interests(message: str) -> list[str]:
    lower = message.lower()
    found: list[str] = []
    for kw, category in INTEREST_KEYWORDS.items():
        if kw in lower and category not in found:
            found.append(category)
    return found


def _extract_time_range(message: str) -> tuple[str, str] | None:
    match = TIME_RANGE_RE.search(message)
    if match:
        h1, m1, period1, h2, m2, period2 = match.groups()
        start = int(h1)
        end = int(h2)
        if period1 and period1.lower() in {"chiều", "tối", "đêm"} and start < 12:
            start += 12
        if period2 and period2.lower() in {"chiều", "tối", "đêm"} and end < 12:
            end += 12
        return f"{start:02d}:{m1 or '00'}", f"{end:02d}:{m2 or '00'}"
    else:
        endpoint_re = re.compile(
            r"lúc\s+(\d{1,2})(?::|\s+giờ\s*)(\d{2})?\s*(sáng|chiều|tối|đêm)?"
            r".*?kết thúc\s+lúc\s+(\d{1,2})(?::|\s+giờ\s*)(\d{2})?\s*(sáng|chiều|tối|đêm)?",
            re.IGNORECASE,
        )
        endpoint_match = endpoint_re.search(message)
        if endpoint_match:
            h1, m1, period1, h2, m2, period2 = endpoint_match.groups()
            start = int(h1) + (12 if period1 and period1.lower() in {"chiều", "tối", "đêm"} and int(h1) < 12 else 0)
            end = int(h2) + (12 if period2 and period2.lower() in {"chiều", "tối", "đêm"} and int(h2) < 12 else 0)
            return f"{start:02d}:{m1 or '00'}", f"{end:02d}:{m2 or '00'}"

        clock_re = re.compile(
            r"(?:từ\s+)?(\d{1,2})(?::|\s+giờ\s*)(\d{2})?\s*(sáng|chiều|tối|đêm)?"
            r"\s*(?:đến|tới|-|->)\s*"
            r"(\d{1,2})(?::|\s+giờ\s*)(\d{2})?\s*(sáng|chiều|tối|đêm)?",
            re.IGNORECASE,
        )
        match = clock_re.search(message)
        if match:
            h1, m1, period1, h2, m2, period2 = match.groups()
            start = int(h1)
            end = int(h2)
            if period1 and period1.lower() in {"chiều", "tối", "đêm"} and start < 12:
                start += 12
            if period2 and period2.lower() in {"chiều", "tối", "đêm"} and end < 12:
                end += 12
            return f"{start:02d}:{m1 or '00'}", f"{end:02d}:{m2 or '00'}"

        word_re = re.compile(
            r"(?:từ\s+)?([a-zà-ỹ]+)\s+giờ\s*(sáng|chiều|tối|đêm)?\s*"
            r"(?:đến|tới)\s*([a-zà-ỹ]+)\s+giờ\s*(sáng|chiều|tối|đêm)?",
            re.IGNORECASE,
        )
        match = word_re.search(message)
        if not match:
            return None
        h1_word, period1, h2_word, period2 = match.groups()
        if h1_word.lower() not in VIETNAMESE_DIGITS or h2_word.lower() not in VIETNAMESE_DIGITS:
            return None
        start = VIETNAMESE_DIGITS[h1_word.lower()]
        end = VIETNAMESE_DIGITS[h2_word.lower()]
        if period1 and period1.lower() in {"chiều", "tối", "đêm"} and start < 12:
            start += 12
        if period2 and period2.lower() in {"chiều", "tối", "đêm"} and end < 12:
            end += 12
        return f"{start:02d}:00", f"{end:02d}:00"
    h1, m1, h2, m2 = match.groups()
    return f"{int(h1):02d}:{m1 or '00'}", f"{int(h2):02d}:{m2 or '00'}"


def _extract_budget(message: str) -> int | None:
    match = BUDGET_RE.search(message)
    if not match:
        return None
    raw = match.group(1).replace(".", "").replace(",", "")
    try:
        value = int(raw)
    except ValueError:
        return None
    if match.group(2).lower() in ("k", "nghìn"):
        value *= 1000
    return value


def _extract_cuisine(message: str, meal: str) -> str | None:
    lower = message.lower()
    meal_markers = ("trưa", "buổi trưa") if meal == "lunch" else ("tối", "buổi tối", "đêm")
    if not any(marker in lower for marker in meal_markers):
        return None
    for keyword, cuisine in CUISINE_KEYWORDS.items():
        if keyword in lower:
            return cuisine
    return None


def _extract_required_museum_count(message: str) -> int:
    match = re.search(r"(?:đúng|chính xác|tổng cộng|tham quan)\s*(\d+)\s*bảo tàng|(?<!\d)(\d+)\s*bảo tàng", message.lower())
    if not match:
        return 0
    return int(next(value for value in match.groups() if value is not None))


def _extract_location(message: str) -> str | None:
    weather_match = re.search(
        r"(?:dự báo\s+)?thời tiết\s+(?:của|ở|tại|khu vực)\s+(.+?)"
        r"(?=\s+(?:của\s+)?ngày\b|\s+(?:vào|lúc)\b|$)",
        message,
        flags=re.IGNORECASE,
    )
    match = weather_match or NEAR_RE.search(message) or AT_RE.search(message) or FROM_RE.search(message)
    if not match:
        return None
    location = match.group(1).strip()
    location = re.split(
        r"\s+(?:thứ|từ|rảnh|ngân sách|đi bằng|mình đi|lúc|trọn vẹn|nguyên ngày|cả ngày|ngày)\b",
        location,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    return location.strip(" ,.") or None


def _extract_travel_mode(message: str) -> str:
    lower = message.lower()
    if "đi bộ" in lower or "di bo" in lower:
        return "walking"
    if "xe máy" in lower or "xe may" in lower:
        return "motorcycle"
    return "car"


def _build_geocode_query(location: str) -> str:
    lower = location.lower()
    known_city_markers = (
        "đà lạt", "da lat", "hồ chí minh", "ho chi minh", "tp.hcm", "tp hcm", "sài gòn", "sai gon",
        "hà nội", "ha noi", "đà nẵng", "da nang", "huế", "hue", "nha trang", "đà lạt",
    )
    if any(marker in lower for marker in known_city_markers):
        return location
    return f"{location}, Ho Chi Minh City"


def _extract_target_date(message: str, today: date_type | None = None) -> date_type:
    """Resolve relative Vietnamese weekdays such as 'thứ Bảy này'."""
    current = today or date_type.today()
    weekdays = {
        "thứ hai": 0,
        "thứ ba": 1,
        "thứ tư": 2,
        "thứ năm": 3,
        "thứ sáu": 4,
        "thứ bảy": 5,
        "chủ nhật": 6,
        "chủ nhat": 6,
    }
    lower = message.lower()
    for label, weekday in weekdays.items():
        if label in lower:
            days_ahead = (weekday - current.weekday()) % 7
            return current + timedelta(days=days_ahead)
    return current


def _extract_explicit_date(message: str, today: date_type | None = None) -> date_type | None:
    current = today or date_type.today()
    month_name_match = re.search(
        r"(?:ngày\s+)?(\d{1,2})\s+tháng\s+(\d{1,2})(?:\s+năm\s+(\d{4}))?",
        message.lower(),
    )
    if month_name_match:
        day, month, year = (int(value) if value else None for value in month_name_match.groups())
        try:
            return date_type(year or current.year, month, day)
        except ValueError:
            return None
    match = re.search(r"(?:ngày\s+)?(\d{1,2})[/-](\d{1,2})(?:[/-](\d{4}))?", message.lower())
    if match:
        day, month, year = (int(value) if value else None for value in match.groups())
        try:
            return date_type(year or current.year, month, day)
        except ValueError:
            return None
    match = re.search(r"ngày\s+(\d{1,2})\b", message.lower())
    if match:
        day = int(match.group(1))
        try:
            return date_type(current.year, current.month, day)
        except ValueError:
            return None
    return None


def _extract_hour(message: str) -> int | None:
    match = re.search(r"(?:lúc|vào|khoảng)\s+(\d{1,2})(?::\d{2}|h)?", message.lower())
    if not match:
        return None
    hour = int(match.group(1))
    return hour if 0 <= hour <= 23 else None


def _is_weather_lookup(message: str) -> bool:
    lower = message.casefold()
    return any(term in lower for term in ("dự báo thời tiết", "thời tiết", "mưa không", "mưa ngày"))


def _is_landmark_lookup(message: str) -> bool:
    lower = message.casefold().strip(" .?!")
    if _is_weather_lookup(message) or _extract_time_range(message):
        return False
    if any(term in lower for term in (
        "đang ở", "rảnh", "xuất phát", "bắt đầu", "đi từ", "đi chơi", "lịch trình",
        "mất bao lâu", "lúc ", "kết thúc", "đổi", "thêm", "xóa", "xoá", "bỏ", "tham quan đúng",
    )):
        return False
    explicit_lookup = any(term in lower for term in (
        "giới thiệu", "cho tôi biết", "thông tin về", "thông tin của", "là gì",
        "tra cứu", "wiki", "wikipedia",
    ))
    if not explicit_lookup and len(lower.split()) > 8:
        return False
    if explicit_lookup and len(lower) <= 120:
        return True
    # Any short direct place name may have a Wikipedia article; do not whitelist names.
    return len(lower) <= 120


def _landmark_query(message: str) -> str:
    query = message.strip()
    query = re.sub(
        r"^(?:hãy\s+)?(?:cho tôi biết(?:\s+về)?|cho tôi thông tin(?:\s+về|\s+của)?|"
        r"giới thiệu(?:\s+về)?|"
        r"thông tin (?:về|của)|tra cứu|wiki|wikipedia)\s+",
        "",
        query,
        flags=re.IGNORECASE,
    )
    query = re.sub(r"\s+là gì\s*$", "", query, flags=re.IGNORECASE)
    return query.strip(" .?!")


async def _handle_landmark_lookup(session_id: str, message: str) -> ChatTurnResult:
    query = _landmark_query(message)
    summary = await wikipedia_service.get_place_summary(query)
    if not summary:
        return ChatTurnResult(session_id=session_id, reply=f"KHÔNG CÓ DỮ LIỆU: chưa tìm thấy bài Wikipedia phù hợp cho '{query}'.")
    url = summary.get("wikipedia_url")
    suffix = f"\nNguồn Wikipedia: {url}" if url else "\nNguồn: Wikipedia tiếng Việt"
    return ChatTurnResult(session_id=session_id, reply=f"**{query}**\n\n{summary['description']}{suffix}")


async def _handle_weather_lookup(session_id: str, state, message: str) -> ChatTurnResult:
    target_date = _extract_explicit_date(message) or state.target_date or date_type.today()
    days_ahead = (target_date - date_type.today()).days
    if days_ahead < 0 or days_ahead > 5:
        return ChatTurnResult(
            session_id=session_id,
            reply="Dự báo thời tiết quá xa ngày hiện tại có thể không chính xác, hệ thống chỉ hỗ trợ tối đa 5 ngày tới.",
            itinerary=state.itinerary,
        )

    latitude = state.constraints.origin_lat if state.constraints else None
    longitude = state.constraints.origin_lon if state.constraints else None
    location_label = state.constraints.origin_label if state.constraints else None
    location = _extract_location(message)
    if location:
        geocoded = await geocode_service.geocode(_build_geocode_query(location))
        if geocoded.error or geocoded.latitude is None or geocoded.longitude is None:
            return ChatTurnResult(session_id=session_id, reply=f"KHÔNG CÓ DỮ LIỆU: không xác định được vị trí '{location}'.", itinerary=state.itinerary)
        latitude, longitude = geocoded.latitude, geocoded.longitude
        location_label = geocoded.display_name or location
    if latitude is None or longitude is None:
        state.pending_weather_query = message
        return ChatTurnResult(session_id=session_id, reply="Bạn hãy cho biết địa điểm cần xem thời tiết.", itinerary=state.itinerary)

    weather = await weather_service.get_hourly_weather(latitude, longitude, target_date)
    if weather.error:
        return ChatTurnResult(session_id=session_id, reply=f"KHÔNG CÓ DỮ LIỆU: {weather.error}", itinerary=state.itinerary)
    points = [point for point in weather.hourly if point.time.date() == target_date]
    if not points:
        return ChatTurnResult(session_id=session_id, reply=f"KHÔNG CÓ DỮ LIỆU thời tiết cho {target_date}.", itinerary=state.itinerary)
    requested_hour = _extract_hour(message)
    if requested_hour is not None:
        point = next((item for item in points if item.time.hour == requested_hour), None)
        if point is None:
            return ChatTurnResult(session_id=session_id, reply=f"KHÔNG CÓ DỮ LIỆU thời tiết lúc {requested_hour}:00 ngày {target_date}.", itinerary=state.itinerary)
        return ChatTurnResult(
            session_id=session_id,
            reply=(
                f"Dự báo tại {location_label} lúc {requested_hour}:00 ngày {target_date}: "
                f"{point.temperature_c}°C, cảm nhận {point.apparent_temperature_c}°C, "
                f"xác suất mưa {point.precipitation_probability_pct}%, lượng mưa {point.precipitation_mm}mm. "
                "Nguồn: Open-Meteo."
            ),
            itinerary=state.itinerary,
        )
    rain = max((point.precipitation_probability_pct or 0) for point in points)
    temperatures = [point.temperature_c for point in points if point.temperature_c is not None]
    if not temperatures:
        return ChatTurnResult(
            session_id=session_id,
            reply=f"KHÔNG CÓ DỮ LIỆU thời tiết đầy đủ cho {target_date}.",
            itinerary=state.itinerary,
        )
    return ChatTurnResult(
        session_id=session_id,
        reply=(
            f"Dự báo tại {location_label} ngày {target_date}: nhiệt độ khoảng "
            f"{min(temperatures):.1f}-{max(temperatures):.1f}°C, xác suất mưa cao nhất {rain:.0f}%. "
            "Nguồn: Open-Meteo."
        ),
        itinerary=state.itinerary,
    )


def _is_afternoon_patch(message: str) -> bool:
    lower = message.lower()
    return "chiều" in lower and any(k in lower for k in ["đổi", "sửa", "xếp lại", "mưa"])


def _is_lunch_patch(message: str) -> bool:
    lower = message.lower()
    return ("trưa" in lower or "chay" in lower) and any(k in lower for k in ["đổi", "sửa", "thay"])


def _is_dinner_patch(message: str) -> bool:
    lower = message.lower()
    return any(k in lower for k in ["ăn tối", "bữa tối", "đi ăn tối", "dinner"]) and any(
        k in lower for k in ["đổi", "sửa", "thay", "thành", "giúp"]
    )


def _find_stop_mentioned(message: str, itinerary: Itinerary) -> str | None:
    lower = message.casefold()
    matches = [stop.name for stop in itinerary.stops if stop.name.casefold() in lower]
    return max(matches, key=len) if matches else None


def _is_named_stop_patch(message: str, itinerary: Itinerary) -> bool:
    lower = message.casefold()
    return _find_stop_mentioned(message, itinerary) is not None and any(
        marker in lower for marker in ("đổi", "thay", "sửa", "địa điểm khác", "điểm khác")
    )


def _is_mobility_update(message: str) -> bool:
    lower = message.lower()
    return any(k in lower for k in ["ông bà", "người già", "cao tuổi", "hạn chế đi bộ", "70 tuổi"])


def _is_itinerary_edit_request(message: str) -> bool:
    lower = message.casefold()
    return any(marker in lower for marker in ("thêm", "xóa", "xoá", "bỏ", "đổi", "sửa", "thay"))


def _edit_action(message: str) -> str | None:
    lower = message.casefold()
    if any(marker in lower for marker in ("xóa", "xoá", "bỏ")):
        return "xóa"
    if "thêm" in lower:
        return "thêm"
    if any(marker in lower for marker in ("đổi", "sửa", "thay")):
        return "sửa"
    return None


def _edit_clarification(message: str) -> str:
    action = _edit_action(message) or "chỉnh sửa"
    if action == "xóa":
        return "Bạn muốn xóa địa điểm nào? Hãy ghi đúng tên điểm trong lịch trình, ví dụ: 'xóa Chợ Bến Thành'."
    if action == "thêm":
        return "Bạn muốn thêm địa điểm/loại điểm nào và vào khung giờ nào? Ví dụ: 'thêm một bảo tàng vào 15h-17h'."
    return "Bạn muốn sửa địa điểm nào hoặc khung giờ nào? Hãy ghi tên điểm, ví dụ: 'đổi Chợ Bến Thành sang điểm khác'."


def _stop_line(s) -> str:
    indoor_note = "trong nhà" if s.indoor else "ngoài trời"
    wiki_note = f" Wikipedia: {s.wikipedia_url}" if s.wikipedia_url else ""
    return (
        f"- {s.arrive}-{s.leave} {s.name} ({indoor_note}, di chuyển {s.travel_minutes_from_prev} phút"
        f" + chờ {s.wait_minutes_from_prev} phút đến khung) "
        f"— {s.reason}. Thời tiết: {s.weather_note}. Nguồn điểm: {s.source}; "
        f"đường đi: {s.route_source or 'không xác định'}"
        f"{' (traffic thật)' if s.traffic_aware else ' (ước lượng/không traffic thật)'}.{wiki_note}"
    )


def _render_new_plan_reply(itinerary: Itinerary, constraints: DayConstraints) -> str:
    lines = [f"Đã lập lịch trình ngày {itinerary.date} với {len(itinerary.stops)} điểm, xuất phát từ {constraints.origin_label}:"]
    if itinerary.budget_vnd_per_person:
        lines.append(
            f"Ngân sách đã ghi nhận: {itinerary.budget_vnd_per_person:,} VNĐ/người. "
            "Chưa thể tính tổng chi phí vì nguồn địa điểm không cung cấp giá thật."
        )
    if itinerary.sunset:
        lines.append(
            f"Thông tin trong ngày: hoàng hôn lúc {itinerary.sunset} "
            f"(nguồn: {itinerary.daily_info_source or 'Open-Meteo'})."
        )
    if itinerary.air_quality_aqi is not None:
        lines.append(
            f"Chất lượng không khí: AQI {itinerary.air_quality_aqi} - "
            f"{itinerary.air_quality_label or 'chưa phân loại'} "
            f"(nguồn: {itinerary.daily_info_source or 'Open-Meteo'})."
        )
    lines.extend(_stop_line(s) for s in itinerary.stops)
    if itinerary.unfilled_slots:
        lines.append(f"Có {itinerary.unfilled_slots} khung giờ không tìm được địa điểm thật phù hợp nên để trống, không bịa thêm.")
    lines.extend(f"Lưu ý: {n}" for n in itinerary.notes)
    return "\n".join(lines)


def _render_patch_reply(itinerary: Itinerary, label: str, extra_note: str | None = None) -> str:
    changed = [s for s in itinerary.stops if s.just_changed]
    lines = [f"Đã cập nhật {label}, các phần khác của lịch trình giữ nguyên:"]
    if changed:
        lines.extend(_stop_line(s) for s in changed)
    else:
        lines.append("(không có điểm nào thay đổi được — có thể do không tìm thêm được dữ liệu thật phù hợp)")
    if extra_note:
        lines.append(extra_note)
    return "\n".join(lines)


async def _handle_new_plan(session_id: str, state: SessionState, message: str) -> ChatTurnResult:
    state.pending_message = f"{state.pending_message} {message}".strip()
    combined_message = state.pending_message
    location_query = _extract_location(combined_message)
    if location_query is None:
        return ChatTurnResult(
            session_id=session_id,
            reply="Bạn đang ở đâu (tên địa điểm/địa chỉ cụ thể) để mình xác định điểm xuất phát?",
        )

    time_range = _extract_time_range(combined_message)
    if time_range is None:
        return ChatTurnResult(
            session_id=session_id,
            reply="Bạn rảnh từ mấy giờ đến mấy giờ? (ví dụ: 'từ 8h đến 20h')",
        )

    geocode_result = await geocode_service.geocode(_build_geocode_query(location_query))
    if geocode_result.error or geocode_result.latitude is None:
        return ChatTurnResult(
            session_id=session_id,
            reply=(
                f"KHÔNG CÓ DỮ LIỆU: không xác định được tọa độ cho '{location_query}' "
                f"({geocode_result.error}). Bạn có thể cho địa chỉ cụ thể hơn không?"
            ),
        )

    free_start, free_end = time_range
    constraints = DayConstraints(
        origin_lat=geocode_result.latitude,
        origin_lon=geocode_result.longitude,
        origin_label=geocode_result.display_name or location_query,
        free_start=free_start,
        free_end=free_end,
        interests=_extract_interests(combined_message),
        budget_vnd_per_person=_extract_budget(combined_message),
        travel_mode=_extract_travel_mode(combined_message),
        required_museum_count=_extract_required_museum_count(combined_message),
        lunch_cuisine=_extract_cuisine(combined_message, "lunch"),
        dinner_cuisine=_extract_cuisine(combined_message, "dinner"),
    )
    target_date = _extract_target_date(combined_message)
    itinerary = await itinerary_builder.build_itinerary(constraints, target_date)
    daily_environment = await weather_service.get_daily_environment(
        constraints.origin_lat, constraints.origin_lon, target_date
    )
    itinerary = itinerary.model_copy(update={
        "sunset": daily_environment.sunset.strftime("%H:%M") if daily_environment.sunset else None,
        "air_quality_aqi": daily_environment.air_quality_aqi,
        "air_quality_label": daily_environment.air_quality_label,
        "daily_info_source": daily_environment.source,
    })
    if daily_environment.error:
        itinerary.notes.append(f"Không lấy đủ thông tin sunset/AQI trong ngày: {daily_environment.error}")

    state.constraints = constraints
    state.itinerary = itinerary
    state.target_date = target_date
    state.pending_message = ""

    return ChatTurnResult(session_id=session_id, reply=_render_new_plan_reply(itinerary, constraints), itinerary=itinerary)


async def _handle_afternoon_patch(session_id: str, state: SessionState, message: str) -> ChatTurnResult:
    assert state.constraints and state.itinerary and state.target_date
    itinerary = await itinerary_builder.patch_afternoon(state.constraints, state.itinerary, state.target_date)
    state.itinerary = itinerary
    reply = _render_patch_reply(itinerary, "buổi chiều (ưu tiên trong nhà theo dự báo mưa)")
    return ChatTurnResult(session_id=session_id, reply=reply, itinerary=itinerary)


async def _handle_lunch_patch(session_id: str, state: SessionState, message: str) -> ChatTurnResult:
    assert state.constraints and state.itinerary and state.target_date
    keyword = "chay" if "chay" in message.lower() else None
    itinerary, error = await itinerary_builder.patch_single_stop(
        state.constraints,
        state.itinerary,
        state.target_date,
        match_categories=["restaurant", "fast_food"],
        time_window=LUNCH_WINDOW,
        name_keyword=keyword,
    )
    if error:
        return ChatTurnResult(session_id=session_id, reply=error, itinerary=state.itinerary)
    state.itinerary = itinerary
    return ChatTurnResult(session_id=session_id, reply=_render_patch_reply(itinerary, "bữa trưa"), itinerary=itinerary)


async def _handle_dinner_patch(session_id: str, state: SessionState, message: str) -> ChatTurnResult:
    assert state.constraints and state.itinerary and state.target_date
    itinerary, error = await itinerary_builder.patch_single_stop(
        state.constraints,
        state.itinerary,
        state.target_date,
        match_categories=["restaurant", "fast_food", "cafe"],
        time_window=DINNER_WINDOW,
    )
    if error:
        return ChatTurnResult(session_id=session_id, reply=error, itinerary=state.itinerary)
    state.itinerary = itinerary
    return ChatTurnResult(session_id=session_id, reply=_render_patch_reply(itinerary, "bữa tối"), itinerary=itinerary)


async def _handle_named_stop_patch(session_id: str, state: SessionState, message: str) -> ChatTurnResult:
    assert state.constraints and state.itinerary and state.target_date
    target_name = _find_stop_mentioned(message, state.itinerary)
    target = next((stop for stop in state.itinerary.stops if stop.name == target_name), None)
    if target is None:
        return ChatTurnResult(session_id=session_id, reply="Không xác định được điểm cần đổi.", itinerary=state.itinerary)

    start = _parse_hhmm_for_handler(target.arrive)
    end = _parse_hhmm_for_handler(target.leave)
    itinerary, error = await itinerary_builder.patch_single_stop(
        state.constraints,
        state.itinerary,
        state.target_date,
        match_categories=[target.category],
        time_window=(start, end),
        target_name=target.name,
    )
    if error:
        return ChatTurnResult(session_id=session_id, reply=error, itinerary=state.itinerary)
    state.itinerary = itinerary
    return ChatTurnResult(
        session_id=session_id,
        reply=_render_patch_reply(itinerary, f"điểm '{target.name}'"),
        itinerary=itinerary,
    )


async def _handle_named_stop_delete(session_id: str, state: SessionState, message: str) -> ChatTurnResult:
    assert state.itinerary
    target_name = _find_stop_mentioned(message, state.itinerary)
    if target_name is None:
        return ChatTurnResult(session_id=session_id, reply=_edit_clarification(message), itinerary=state.itinerary)
    if state.constraints and state.target_date:
        deleted, error = await itinerary_builder.delete_stop(
            state.constraints, state.itinerary, state.target_date, target_name
        )
    else:
        remaining = [stop for stop in state.itinerary.stops if stop.name != target_name]
        deleted = state.itinerary.model_copy(update={"stops": remaining})
        deleted.notes = list(deleted.notes) + [
            "Đã xóa điểm, nhưng chưa thể tính lại route kế tiếp vì session thiếu ràng buộc xuất phát."
        ]
        error = None
    if error:
        return ChatTurnResult(session_id=session_id, reply=error, itinerary=state.itinerary)
    state.itinerary = deleted
    return ChatTurnResult(
        session_id=session_id,
        reply=f"Đã xóa '{target_name}' khỏi lịch trình. Khung giờ đó được để trống, không tự thêm địa điểm khác.",
        itinerary=deleted,
    )


async def _handle_add_stop(session_id: str, state: SessionState, message: str) -> ChatTurnResult:
    assert state.constraints and state.itinerary and state.target_date
    time_range = _extract_time_range(message)
    categories = _extract_interests(message)
    if time_range is None or not categories:
        return ChatTurnResult(session_id=session_id, reply=_edit_clarification(message), itinerary=state.itinerary)

    start, end = (time(int(value[:2]), int(value[3:])) for value in time_range)
    block_kind = "meal" if any(category in itinerary_builder.FOOD_CATEGORIES for category in categories) else "sight"
    block = itinerary_builder.TimeBlock("Điểm bổ sung", start, end, block_kind)
    existing = sorted(state.itinerary.stops, key=lambda stop: stop.arrive)
    insertion_index = next((i for i, stop in enumerate(existing) if _parse_hhmm_for_handler(stop.arrive) >= start), len(existing))
    previous = existing[insertion_index - 1] if insertion_index else None
    next_stop = existing[insertion_index] if insertion_index < len(existing) else None
    previous_pos = (
        (previous.lat, previous.lon) if previous else (state.constraints.origin_lat, state.constraints.origin_lon)
    )
    previous_leave = (
        _parse_hhmm_for_handler(previous.leave) if previous else _parse_hhmm_for_handler(state.constraints.free_start)
    )
    candidate_constraints = state.constraints.model_copy(
        update={"interests": list(dict.fromkeys(state.constraints.interests + categories))}
    )
    built = await itinerary_builder._build_stop(
        block,
        previous_pos,
        datetime.combine(state.target_date, previous_leave),
        state.target_date,
        candidate_constraints,
        {stop.name for stop in existing},
        0,
        previous_category=previous.category if previous else None,
    )
    if built is None:
        return ChatTurnResult(
            session_id=session_id,
            reply="KHÔNG CÓ DỮ LIỆU: không tìm thấy địa điểm thật phù hợp để thêm vào khung giờ này.",
            itinerary=state.itinerary,
        )
    new_stop, _, _ = built
    new_stop.just_changed = True
    if next_stop and _parse_hhmm_for_handler(new_stop.leave) > _parse_hhmm_for_handler(next_stop.arrive):
        return ChatTurnResult(
            session_id=session_id,
            reply="Không thể thêm điểm: khung giờ mới làm chồng lên điểm kế tiếp trong lịch trình.",
            itinerary=state.itinerary,
        )
    updated_stops = existing[:insertion_index] + [new_stop] + existing[insertion_index:]
    updated = state.itinerary.model_copy(update={"stops": updated_stops})
    updated.notes = list(updated.notes) + [f"Đã thêm {new_stop.name} vào khung {start.strftime('%H:%M')}-{end.strftime('%H:%M')} theo yêu cầu."]
    state.itinerary = updated
    return ChatTurnResult(
        session_id=session_id,
        reply=_render_patch_reply(updated, f"thêm điểm '{new_stop.name}'"),
        itinerary=updated,
    )


def _parse_hhmm_for_handler(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


async def _handle_mobility_update(session_id: str, state: SessionState, message: str) -> ChatTurnResult:
    assert state.constraints and state.itinerary and state.target_date
    state.constraints = state.constraints.model_copy(update={"mobility_limited": True, "travel_mode": "car"})
    itinerary = await itinerary_builder.apply_mobility_constraint(state.constraints, state.itinerary, state.target_date)
    state.itinerary = itinerary
    extra = "Ghi chú: giảm số điểm, chuyển sang ưu tiên ô tô, thêm thời gian nghỉ giữa các điểm."
    reply = _render_patch_reply(itinerary, "lịch trình cho phù hợp với người lớn tuổi/hạn chế đi bộ", extra)
    return ChatTurnResult(session_id=session_id, reply=reply, itinerary=itinerary)


async def _handle_message(session_id: str, message: str) -> ChatTurnResult:
    state = get_session(session_id)

    if state.pending_weather_query:
        follow_up_location = _extract_location(message)
        if follow_up_location is None and not _is_weather_lookup(message) and len(message.split()) <= 12:
            follow_up_location = message.strip(" .?!")
        if follow_up_location:
            original_query = state.pending_weather_query
            state.pending_weather_query = ""
            return await _handle_weather_lookup(
                session_id,
                state,
                f"{original_query} ở {follow_up_location}",
            )

    if _is_landmark_lookup(message):
        return await _handle_landmark_lookup(session_id, message)
    if _is_weather_lookup(message):
        return await _handle_weather_lookup(session_id, state, message)

    if state.itinerary is None:
        return await _handle_new_plan(session_id, state, message)
    if _is_afternoon_patch(message):
        return await _handle_afternoon_patch(session_id, state, message)
    if _is_lunch_patch(message):
        return await _handle_lunch_patch(session_id, state, message)
    if _is_dinner_patch(message):
        return await _handle_dinner_patch(session_id, state, message)
    if _is_named_stop_patch(message, state.itinerary):
        return await _handle_named_stop_patch(session_id, state, message)
    if _edit_action(message) == "thêm":
        return await _handle_add_stop(session_id, state, message)
    if _edit_action(message) == "xóa":
        if _find_stop_mentioned(message, state.itinerary):
            return await _handle_named_stop_delete(session_id, state, message)
        return ChatTurnResult(session_id=session_id, reply=_edit_clarification(message), itinerary=state.itinerary)
    if _is_itinerary_edit_request(message):
        return ChatTurnResult(session_id=session_id, reply=_edit_clarification(message), itinerary=state.itinerary)
    if _is_mobility_update(message):
        return await _handle_mobility_update(session_id, state, message)

    # Câu hỏi mở (so sánh giờ khởi hành, tra cứu địa điểm hiếm/không có dữ liệu...) -> LLM agent + tool.
    try:
        reply = await run_agent(message)
    except Exception as exc:
        logger.exception("Open-ended agent request failed: %s", exc)
        reply = "KHÔNG CÓ DỮ LIỆU: trợ lý AI tạm thời không xử lý được yêu cầu này. Vui lòng thử lại sau."
    return ChatTurnResult(session_id=session_id, reply=reply, itinerary=state.itinerary)


async def handle_message(session_id: str, message: str) -> ChatTurnResult:
    async with get_session_lock(session_id):
        return await _handle_message(session_id, message)
