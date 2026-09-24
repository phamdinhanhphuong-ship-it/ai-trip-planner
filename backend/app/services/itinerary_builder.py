from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime, time, timedelta

from app.models.itinerary import DayConstraints, Itinerary, Stop
from app.models.places import Place
from app.services import places_service, routing_service, traffic_heuristic, weather_service
from app.services.opening_hours import is_open_at

FOOD_CATEGORIES = ["restaurant", "cafe", "fast_food"]
SIGHT_CATEGORIES = ["museum", "attraction", "viewpoint", "historic", "park", "temple"]
MAIN_MEAL_TYPES = {"breakfast", "lunch", "dinner"}
SNACK_MEAL_TYPES = {"snack"}
MAIN_MEAL_CATEGORIES = {"restaurant", "fast_food"}
SNACK_CATEGORIES = {"cafe", "dessert", "snack", "bar"}
MEAL_WINDOWS = {
    "breakfast": (time(6, 0), time(9, 0)),
    "lunch": (time(11, 0), time(14, 0)),
    "dinner": (time(18, 0), time(21, 0)),
}

# Suy ra trong nhà/ngoài trời từ category — không có tag "indoor" trực tiếp trong OSM,
# đây là giả định hợp lý theo loại hình, ghi rõ trong README.
INDOOR_BY_CATEGORY: dict[str, bool] = {
    "museum": True,
    "cafe": True,
    "restaurant": True,
    "fast_food": True,
    "temple": True,
    "park": False,
    "viewpoint": False,
    "attraction": False,
    "historic": False,
}

# Ngưỡng quyết định né nắng/mưa (dựa theo mô tả "nắng gắt >35°C", "mưa dồn chiều tối" trong đề).
HEAT_INDOOR_THRESHOLD_C = 34.0
RAIN_INDOOR_PROBABILITY_PCT = 50.0
# Thêm thời gian nghỉ giữa các điểm khi đi cùng người lớn tuổi / hạn chế đi bộ.
MOBILITY_REST_BUFFER_MIN = 15


async def _get_route(origin, destination, depart_at, travel_mode):
    if travel_mode == "car":
        return await routing_service.get_route(origin, destination, depart_at)
    return await routing_service.get_route(origin, destination, depart_at, travel_mode)


@dataclass
class TimeBlock:
    label: str
    start: time
    end: time
    kind: str  # "meal" | "sight"
    meal_type: str | None = None


def _expand_museum_blocks(blocks: list[TimeBlock], requested_count: int) -> list[TimeBlock]:
    """Split sight windows into enough deterministic slots for N requested museums."""
    if requested_count <= 0:
        return blocks
    sight_indexes = [index for index, block in enumerate(blocks) if block.kind == "sight"]
    if not sight_indexes:
        return blocks

    count = max(requested_count, len(sight_indexes))
    base, remainder = divmod(count, len(sight_indexes))
    allocation = [base + (1 if index < remainder else 0) for index in range(len(sight_indexes))]
    expanded: list[TimeBlock] = []
    sight_number = 0
    allocation_by_index = dict(zip(sight_indexes, allocation))
    for index, block in enumerate(blocks):
        slots = allocation_by_index.get(index)
        if slots is None:
            expanded.append(block)
            continue
        total_minutes = int((datetime.combine(date_type.today(), block.end) - datetime.combine(date_type.today(), block.start)).total_seconds() // 60)
        slot_minutes = max(30, total_minutes // slots)
        cursor = block.start
        for slot in range(slots):
            remaining_slots = slots - slot - 1
            end_minutes = cursor.hour * 60 + cursor.minute + (total_minutes - slot_minutes * remaining_slots - (cursor.hour * 60 + cursor.minute - block.start.hour * 60 - block.start.minute))
            end = time(end_minutes // 60, end_minutes % 60)
            sight_number += 1
            expanded.append(TimeBlock(f"{block.label} ({sight_number}/{count})", cursor, end, "sight"))
            cursor = end
    return expanded


DEFAULT_BLOCKS: list[TimeBlock] = [
    TimeBlock("Ăn sáng", time(7, 30), time(9, 0), "meal", "breakfast"),
    TimeBlock("Tham quan buổi sáng", time(9, 30), time(11, 0), "sight"),
    TimeBlock("Ăn trưa", time(12, 0), time(13, 30), "meal", "lunch"),
    TimeBlock("Tham quan buổi chiều", time(13, 0), time(16, 0), "sight"),
    TimeBlock("Giải lao / cà phê", time(16, 30), time(18, 0), "meal", "snack"),
    TimeBlock("Ăn tối", time(18, 0), time(20, 0), "meal", "dinner"),
]

# Ít điểm hơn, nhiều thời gian nghỉ hơn — dùng khi mobility_limited=True (vd. đi cùng người lớn tuổi).
MOBILITY_LIMITED_BLOCKS: list[TimeBlock] = [
    TimeBlock("Ăn sáng", time(7, 30), time(9, 0), "meal", "breakfast"),
    TimeBlock("Tham quan buổi sáng", time(9, 30), time(11, 0), "sight"),
    TimeBlock("Ăn trưa", time(12, 0), time(13, 30), "meal", "lunch"),
    TimeBlock("Nghỉ / cà phê", time(15, 0), time(16, 30), "meal", "snack"),
    TimeBlock("Ăn tối", time(18, 0), time(20, 0), "meal", "dinner"),
]


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def _clip_blocks(blocks: list[TimeBlock], free_start: time, free_end: time) -> list[TimeBlock]:
    clipped = []
    for b in blocks:
        if b.end <= free_start or b.start >= free_end:
            continue
        new_start = max(b.start, free_start)
        new_end = min(b.end, free_end)
        if new_start >= new_end:
            continue
        clipped.append(TimeBlock(b.label, new_start, new_end, b.kind, b.meal_type))
    return clipped


def _categories_for_block(block: TimeBlock, interests: list[str]) -> list[str]:
    if block.kind == "meal":
        if block.meal_type in SNACK_MEAL_TYPES:
            wanted = [c for c in FOOD_CATEGORIES if c in interests and c in SNACK_CATEGORIES]
            return wanted or ["cafe"]
        wanted = [c for c in FOOD_CATEGORIES if c in interests and c in MAIN_MEAL_CATEGORIES]
        return wanted or ["restaurant", "fast_food"]
    wanted = [c for c in SIGHT_CATEGORIES if c in interests]
    return wanted or SIGHT_CATEGORIES


def _prefers_indoor(weather_point) -> bool:
    if weather_point is None:
        return False
    rain = weather_point.precipitation_probability_pct
    heat = weather_point.apparent_temperature_c
    if rain is not None and rain >= RAIN_INDOOR_PROBABILITY_PCT:
        return True
    if heat is not None and heat >= HEAT_INDOOR_THRESHOLD_C:
        return True
    return False


def _weather_note(point, error: str | None) -> str:
    if error:
        return f"Không có dữ liệu thời tiết: {error}"
    if point is None:
        return "Không có dữ liệu thời tiết cho đúng khung giờ này"
    apparent = f", cảm nhận {point.apparent_temperature_c}°C" if point.apparent_temperature_c is not None else ""
    return (
        f"{point.temperature_c}°C{apparent}, xác suất mưa {point.precipitation_probability_pct}% "
        "(nguồn: Open-Meteo)"
    )


async def _weather_at(lat: float, lon: float, when: datetime):
    result = await weather_service.get_hourly_weather(lat, lon, when.date())
    if result.error:
        return None, result.error
    return weather_service.get_weather_at_hour(result, when), None


async def _find_candidate(
    current_pos: tuple[float, float],
    categories: list[str],
    when: datetime,
    radius: int,
    used_names: set[str],
    name_keyword: str | None = None,
    cuisine: str | None = None,
    excluded_categories: set[str] | None = None,
    previous_category: str | None = None,
) -> tuple[Place, bool] | None:
    """Trả (place, hours_known). hours_known=False nghĩa là chưa xác minh được giờ mở cửa."""
    if cuisine:
        places_result = await places_service.search_places(
            current_pos[0], current_pos[1], radius, categories, cuisine=cuisine
        )
    else:
        places_result = await places_service.search_places(current_pos[0], current_pos[1], radius, categories)
    if not places_result.places:
        return None
    within = places_service.filter_by_distance(places_result.places, radius)
    if name_keyword:
        keyword = name_keyword.strip().lower()
        within = [p for p in within if keyword in p.name.lower()]
    if cuisine:
        cuisine_terms = {
            "thai": ("thai", "thái", "siam"),
            "european": ("europe", "âu", "french", "italian", "ý"),
            "italian": ("italian", "ý", "pizza", "pasta"),
        }.get(cuisine, (cuisine,))
        within = [
            p for p in within
            if (p.cuisine and any(term in p.cuisine.lower() for term in cuisine_terms))
            or any(term in p.name.lower() for term in cuisine_terms)
        ]
    open_places, unknown_places = places_service.filter_open_at(within, when)
    excluded_categories = excluded_categories or set()
    pool = [
        p for p in open_places
        if p.name not in used_names and p.category not in excluded_categories
    ]
    fallback_pool = [
        p for p in unknown_places
        if p.name not in used_names and p.category not in excluded_categories
    ]
    if previous_category:
        pool = [p for p in pool if p.category != previous_category]
        fallback_pool = [p for p in fallback_pool if p.category != previous_category]
    chosen_pool = pool or fallback_pool
    if not chosen_pool:
        return None
    chosen_pool = sorted(chosen_pool, key=lambda p: p.distance_m or 0)
    place = chosen_pool[0]
    return place, place in pool


def _build_reason(
    block_label: str,
    interests: list[str],
    prefer_indoor: bool,
    place_indoor: bool,
    hours_known: bool,
    cuisine: str | None = None,
) -> str:
    if cuisine:
        parts = [f"Phù hợp khung '{block_label}', đã lọc theo ẩm thực {cuisine}"]
    else:
        parts = [f"Phù hợp khung '{block_label}' và sở thích ({', '.join(interests) or 'chưa chọn cụ thể'})"]
    if prefer_indoor and place_indoor:
        parts.append("ưu tiên trong nhà để né mưa/nắng gắt theo dự báo")
    elif prefer_indoor and not place_indoor:
        parts.append("không tìm được lựa chọn trong nhà phù hợp sở thích nên vẫn giữ ngoài trời — cân nhắc mang theo ô/nước")
    if not hours_known:
        parts.append("CHƯA XÁC MINH giờ mở cửa từ nguồn dữ liệu, không coi là chắc chắn đang mở")
    return "; ".join(parts)


async def _build_stop(
    block: TimeBlock,
    current_pos: tuple[float, float],
    prev_leave_dt: datetime,
    target_date: date_type,
    constraints: DayConstraints,
    used_names: set[str],
    rest_buffer_min: int,
    name_keyword: str | None = None,
    force_museum: bool = False,
    excluded_categories: set[str] | None = None,
    previous_category: str | None = None,
) -> tuple[Stop, tuple[float, float], datetime] | None:
    block_start_dt = datetime.combine(target_date, block.start)
    block_end_dt = datetime.combine(target_date, block.end)
    probe_dt = max(prev_leave_dt, block_start_dt)

    if force_museum:
        categories = ["museum"]
    else:
        categories = _categories_for_block(block, constraints.interests)
    cuisine = None
    if block.label == "Ăn trưa":
        cuisine = constraints.lunch_cuisine
    elif block.label == "Ăn tối":
        cuisine = constraints.dinner_cuisine
    if not categories:
        return None

    weather_point, weather_error = await _weather_at(current_pos[0], current_pos[1], probe_dt)
    prefer_indoor = _prefers_indoor(weather_point)
    search_categories = categories
    if prefer_indoor:
        indoor_only = [c for c in categories if INDOOR_BY_CATEGORY.get(c, True)]
        if indoor_only:
            search_categories = indoor_only

    radius = 700 if constraints.mobility_limited else 1500
    found = await _find_candidate(
        current_pos, search_categories, probe_dt, radius, used_names, name_keyword, cuisine,
        excluded_categories=excluded_categories, previous_category=previous_category,
    )
    if found is None:
        return None
    place, hours_known = found

    rain_mm_h = weather_point.precipitation_mm if weather_point and weather_point.precipitation_mm is not None else 0.0
    route = await _get_route(current_pos, (place.latitude, place.longitude), prev_leave_dt, constraints.travel_mode)
    route = traffic_heuristic.apply_weather_traffic_adjustment(route, rain_mm_h)
    travel_min = round((route.duration_s or 0) / 60) if route.duration_s is not None else 0

    route_arrive_dt = prev_leave_dt + timedelta(minutes=travel_min)
    arrive_dt = route_arrive_dt
    if arrive_dt < block_start_dt:
        arrive_dt = block_start_dt
    if arrive_dt >= block_end_dt:
        return None
    if hours_known and not is_open_at(place.opening_hours_raw, arrive_dt):
        return None

    leave_dt = min(arrive_dt + (block_end_dt - block_start_dt), block_end_dt)
    if rest_buffer_min:
        leave_dt = min(leave_dt + timedelta(minutes=rest_buffer_min), block_end_dt + timedelta(minutes=rest_buffer_min))

    place_indoor = INDOOR_BY_CATEGORY.get(place.category, True)
    stop = Stop(
        name=place.name,
        category=place.category,
        indoor=place_indoor,
        lat=place.latitude,
        lon=place.longitude,
        arrive=arrive_dt.strftime("%H:%M"),
        leave=leave_dt.strftime("%H:%M"),
        travel_minutes_from_prev=travel_min,
        wait_minutes_from_prev=round(max(0, (arrive_dt - route_arrive_dt).total_seconds() / 60), 1),
        weather_note=_weather_note(weather_point, weather_error),
        reason=_build_reason(block.label, constraints.interests, prefer_indoor, place_indoor, hours_known, cuisine),
        source=place.source,
        meal_type=block.meal_type,
        route_geometry=route.geometry,
        wikipedia_url=place.wikipedia_url,
        wikipedia_description=place.description,
        route_source=route.source,
        traffic_aware=route.traffic_aware,
    )
    return stop, (place.latitude, place.longitude), leave_dt


def _stop_meal_type(stop: Stop) -> str | None:
    if stop.meal_type:
        return stop.meal_type
    arrive = _parse_hhmm(stop.arrive)
    for meal_type, (start, end) in MEAL_WINDOWS.items():
        if start <= arrive < end:
            return meal_type
    return None


def validate_meal_coverage(itinerary: Itinerary, free_start: str, free_end: str) -> list[str]:
    """Return transparent warnings for meal coverage, spacing, variety, and budget-related gaps."""
    start = _parse_hhmm(free_start)
    end = _parse_hhmm(free_end)
    notes: list[str] = []
    main_meals = [stop for stop in itinerary.stops if _stop_meal_type(stop) in MAIN_MEAL_TYPES]

    for meal_type, (window_start, window_end) in MEAL_WINDOWS.items():
        if start <= window_start and end >= window_end:
            covered = any(
                _stop_meal_type(stop) == meal_type
                and window_start <= _parse_hhmm(stop.arrive) < window_end
                for stop in itinerary.stops
            )
            if not covered:
                label = {"breakfast": "bữa sáng", "lunch": "bữa trưa", "dinner": "bữa tối"}[meal_type]
                notes.append(f"Không tìm được địa điểm phù hợp cho {label} trong khung giờ yêu cầu — bỏ trống, không bịa.")

    main_meals.sort(key=lambda stop: _parse_hhmm(stop.arrive))
    for previous, current in zip(main_meals, main_meals[1:]):
        gap_minutes = (
            datetime.combine(itinerary.date, _parse_hhmm(current.arrive))
            - datetime.combine(itinerary.date, _parse_hhmm(previous.arrive))
        ).total_seconds() / 60
        if gap_minutes < 150 or gap_minutes > 360:
            notes.append(
                f"Khoảng cách giữa các bữa chính ({previous.meal_type or previous.arrive} → "
                f"{current.meal_type or current.arrive}) là {round(gap_minutes)} phút, "
                "ngoài ngưỡng khuyến nghị 150–360 phút."
            )

    for previous, current in zip(itinerary.stops, itinerary.stops[1:]):
        if previous.category == current.category and previous.category not in {"museum", "attraction"}:
            notes.append(f"Hai điểm liên tiếp cùng category '{current.category}', lịch trình có thể thiếu đa dạng.")

    food_count = sum(stop.category in {"restaurant", "fast_food", "cafe", "dessert"} for stop in itinerary.stops)
    if itinerary.stops and food_count / len(itinerary.stops) > 0.6:
        notes.append(
            f"Các điểm ăn uống chiếm {food_count}/{len(itinerary.stops)} điểm, vượt tỷ lệ khuyến nghị khoảng 60%; "
            "nên ưu tiên thêm điểm tham quan/giải trí."
        )
    return notes


async def build_itinerary(
    constraints: DayConstraints,
    target_date: date_type,
    keep_stops: list[Stop] | None = None,
) -> Itinerary:
    """Dựng lịch trình cả ngày. `keep_stops` (nếu có) được giữ nguyên đúng khung giờ cũ —
    dùng khi patch cục bộ (đổi 1 điểm, đổi ràng buộc) mà không được generate lại từ đầu."""
    free_start = _parse_hhmm(constraints.free_start)
    free_end = _parse_hhmm(constraints.free_end)
    template = MOBILITY_LIMITED_BLOCKS if constraints.mobility_limited else DEFAULT_BLOCKS
    blocks = _clip_blocks(template, free_start, free_end)
    blocks = _expand_museum_blocks(blocks, constraints.required_museum_count)
    rest_buffer = MOBILITY_REST_BUFFER_MIN if constraints.mobility_limited else 0

    stops: list[Stop] = []
    used_names: set[str] = set()
    current_pos = (constraints.origin_lat, constraints.origin_lon)
    prev_leave_dt = datetime.combine(target_date, free_start)
    unfilled = 0
    notes: list[str] = []

    keep_by_time = sorted(keep_stops or [], key=lambda s: s.arrive)
    museum_slots_remaining = constraints.required_museum_count
    used_meal_types: set[str] = set()

    for block in blocks:
        block_start_dt = datetime.combine(target_date, block.start)
        block_end_dt = datetime.combine(target_date, block.end)

        if block.meal_type in MAIN_MEAL_TYPES and block.meal_type in used_meal_types:
            unfilled += 1
            notes.append(f"Đã có {block.meal_type} trong ngày — bỏ qua khung ăn trùng, không xếp thêm.")
            continue

        matched_keep = next(
            (s for s in keep_by_time if block_start_dt.time() <= _parse_hhmm(s.arrive) < block_end_dt.time()),
            None,
        )
        if matched_keep is not None:
            keep_by_time.remove(matched_keep)
            arrive_dt = datetime.combine(target_date, _parse_hhmm(matched_keep.arrive))
            kept = matched_keep.model_copy(update={"just_changed": False})
            stops.append(kept)
            kept_meal_type = _stop_meal_type(kept)
            if kept_meal_type in MAIN_MEAL_TYPES:
                used_meal_types.add(kept_meal_type)
            used_names.add(kept.name)
            current_pos = (kept.lat, kept.lon)
            prev_leave_dt = datetime.combine(target_date, _parse_hhmm(kept.leave))
            continue

        force_museum = block.kind == "sight" and museum_slots_remaining > 0
        built = await _build_stop(
            block, current_pos, prev_leave_dt, target_date, constraints, used_names, rest_buffer,
            force_museum=force_museum,
            previous_category=stops[-1].category if stops else None,
        )
        if force_museum:
            museum_slots_remaining -= 1
        if built is None:
            unfilled += 1
            notes.append(
                f"Không tìm được địa điểm thật phù hợp cho khung '{block.label}' "
                f"({block.start.strftime('%H:%M')}-{block.end.strftime('%H:%M')}) — bỏ trống thay vì bịa."
            )
            continue

        stop, current_pos, prev_leave_dt = built
        stops.append(stop)
        used_names.add(stop.name)
        if stop.meal_type in MAIN_MEAL_TYPES:
            used_meal_types.add(stop.meal_type)

    if constraints.budget_vnd_per_person:
        notes.append(
            f"Ngân sách người dùng: {constraints.budget_vnd_per_person:,} VNĐ/người. "
            "Nguồn dữ liệu địa điểm hiện tại không cung cấp giá thật, nên chưa thể tính tổng chi phí "
            "hay khẳng định lịch trình nằm trong ngân sách."
        )

    actual_museums = sum(stop.category == "museum" for stop in stops)
    if constraints.required_museum_count and actual_museums != constraints.required_museum_count:
        notes.append(
            f"Yêu cầu {constraints.required_museum_count} bảo tàng nhưng chỉ xếp được {actual_museums} "
            "điểm có dữ liệu phù hợp trong khung giờ và giờ mở cửa hiện có."
        )
    itinerary = Itinerary(
        date=target_date,
        stops=stops,
        unfilled_slots=unfilled,
        notes=notes,
        budget_vnd_per_person=constraints.budget_vnd_per_person,
        estimated_cost_vnd=None,
    )
    itinerary.notes.extend(validate_meal_coverage(itinerary, constraints.free_start, constraints.free_end))
    return itinerary


async def patch_afternoon(
    constraints: DayConstraints,
    itinerary: Itinerary,
    target_date: date_type,
    cutoff: time = time(13, 0),
) -> Itinerary:
    """Chỉ xếp lại các khung sau `cutoff`; giữ nguyên các điểm trước đó (vd. buổi sáng)."""
    kept = [s for s in itinerary.stops if _parse_hhmm(s.arrive) < cutoff]
    rebuilt = await build_itinerary(constraints, target_date, keep_stops=kept)
    rebuilt = rebuilt.model_copy(update={
        "sunset": itinerary.sunset,
        "air_quality_aqi": itinerary.air_quality_aqi,
        "air_quality_label": itinerary.air_quality_label,
        "daily_info_source": itinerary.daily_info_source,
    })
    kept_names = {s.name for s in kept}
    for s in rebuilt.stops:
        if s.name not in kept_names:
            s.just_changed = True
    return rebuilt


async def patch_single_stop(
    constraints: DayConstraints,
    itinerary: Itinerary,
    target_date: date_type,
    match_categories: list[str],
    time_window: tuple[time, time],
    name_keyword: str | None = None,
    target_name: str | None = None,
) -> tuple[Itinerary, str | None]:
    """Thay đúng 1 điểm khớp category + khung giờ, giữ nguyên mọi điểm khác + ràng buộc cũ.

    Trả về (itinerary_mới, thông_báo_lỗi). Nếu không tìm được ứng viên thật phù hợp,
    itinerary giữ nguyên và trả lỗi rõ ràng — không bịa điểm thay thế.
    """
    target_index = next(
        (
            i
            for i, s in enumerate(itinerary.stops)
            if (
                (target_name is None or s.name.casefold() == target_name.casefold())
                and s.category in match_categories
                and time_window[0] <= _parse_hhmm(s.arrive) < time_window[1]
            )
        ),
        None,
    )
    if target_index is None:
        return itinerary, "Không tìm thấy điểm nào trong lịch trình hiện tại khớp khung giờ/loại cần đổi."

    old_stop = itinerary.stops[target_index]
    prev_pos = (
        (itinerary.stops[target_index - 1].lat, itinerary.stops[target_index - 1].lon)
        if target_index > 0
        else (constraints.origin_lat, constraints.origin_lon)
    )
    prev_leave_dt = (
        datetime.combine(target_date, _parse_hhmm(itinerary.stops[target_index - 1].leave))
        if target_index > 0
        else datetime.combine(target_date, _parse_hhmm(constraints.free_start))
    )
    used_names = {s.name for s in itinerary.stops if s.name != old_stop.name}
    arrive_dt = datetime.combine(target_date, _parse_hhmm(old_stop.arrive))
    block_end_dt = datetime.combine(target_date, _parse_hhmm(old_stop.leave))
    fake_block = TimeBlock(old_stop.category, arrive_dt.time(), block_end_dt.time(), "meal", old_stop.meal_type)

    weather_point, weather_error = await _weather_at(prev_pos[0], prev_pos[1], arrive_dt)
    radius = 700 if constraints.mobility_limited else 1500
    found = await _find_candidate(prev_pos, match_categories, arrive_dt, radius, used_names, name_keyword)
    if found is None:
        keyword_note = f" tên chứa '{name_keyword}'" if name_keyword else ""
        return itinerary, f"KHÔNG CÓ DỮ LIỆU: không tìm thấy địa điểm{keyword_note} phù hợp gần đó để thay thế."

    place, hours_known = found
    rain_mm_h = weather_point.precipitation_mm if weather_point and weather_point.precipitation_mm is not None else 0.0
    route = await _get_route(prev_pos, (place.latitude, place.longitude), prev_leave_dt, constraints.travel_mode)
    route = traffic_heuristic.apply_weather_traffic_adjustment(route, rain_mm_h)
    travel_min = round((route.duration_s or 0) / 60) if route.duration_s is not None else 0
    route_arrive_dt = prev_leave_dt + timedelta(minutes=travel_min)
    wait_minutes = round(max(0, (arrive_dt - route_arrive_dt).total_seconds() / 60), 1)

    new_stop = Stop(
        name=place.name,
        category=place.category,
        indoor=INDOOR_BY_CATEGORY.get(place.category, True),
        lat=place.latitude,
        lon=place.longitude,
        arrive=old_stop.arrive,
        leave=old_stop.leave,
        travel_minutes_from_prev=travel_min,
        wait_minutes_from_prev=wait_minutes,
        weather_note=_weather_note(weather_point, weather_error),
        reason=_build_reason(fake_block.label, constraints.interests, False, INDOOR_BY_CATEGORY.get(place.category, True), hours_known)
        + " (theo yêu cầu đổi điểm của bạn)",
        source=place.source,
        meal_type=old_stop.meal_type,
        route_geometry=route.geometry,
        wikipedia_url=place.wikipedia_url,
        wikipedia_description=place.description,
        route_source=route.source,
        traffic_aware=route.traffic_aware,
        just_changed=True,
    )

    new_stops = list(itinerary.stops)
    new_stops = [stop.model_copy(update={"just_changed": False}) for stop in new_stops]
    new_stops[target_index] = new_stop

    if target_index + 1 < len(new_stops):
        nxt = new_stops[target_index + 1]
        nxt_arrive_dt = datetime.combine(target_date, _parse_hhmm(nxt.arrive))
        next_route = await _get_route(
            (new_stop.lat, new_stop.lon), (nxt.lat, nxt.lon),
            datetime.combine(target_date, _parse_hhmm(new_stop.leave)), constraints.travel_mode
        )
        next_rain_point, _ = await _weather_at(new_stop.lat, new_stop.lon, nxt_arrive_dt)
        next_rain = next_rain_point.precipitation_mm if next_rain_point and next_rain_point.precipitation_mm is not None else 0.0
        next_route = traffic_heuristic.apply_weather_traffic_adjustment(next_route, next_rain)
        next_travel_minutes = (
            round((next_route.duration_s or 0) / 60)
            if next_route.duration_s is not None
            else nxt.travel_minutes_from_prev
        )
        next_route_arrive_dt = datetime.combine(target_date, _parse_hhmm(new_stop.leave)) + timedelta(
            minutes=next_travel_minutes
        )
        if next_route_arrive_dt > nxt_arrive_dt:
            return itinerary, "Không thể đổi điểm: tuyến đường mới làm chồng lên khung giờ của điểm kế tiếp."
        new_stops[target_index + 1] = nxt.model_copy(
            update={
                "travel_minutes_from_prev": next_travel_minutes,
                "wait_minutes_from_prev": round(max(0, (nxt_arrive_dt - next_route_arrive_dt).total_seconds() / 60), 1),
                "route_source": next_route.source,
                "traffic_aware": next_route.traffic_aware,
                "route_geometry": next_route.geometry,
            }
        )

    return Itinerary(
        date=itinerary.date,
        stops=new_stops,
        unfilled_slots=itinerary.unfilled_slots,
        notes=itinerary.notes,
        budget_vnd_per_person=itinerary.budget_vnd_per_person,
        estimated_cost_vnd=itinerary.estimated_cost_vnd,
        sunset=itinerary.sunset,
        air_quality_aqi=itinerary.air_quality_aqi,
        air_quality_label=itinerary.air_quality_label,
        daily_info_source=itinerary.daily_info_source,
    ), None


async def delete_stop(
    constraints: DayConstraints,
    itinerary: Itinerary,
    target_date: date_type,
    target_name: str,
) -> tuple[Itinerary, str | None]:
    """Remove one stop and recalculate the following leg instead of keeping stale route data."""
    target_index = next(
        (index for index, stop in enumerate(itinerary.stops) if stop.name.casefold() == target_name.casefold()),
        None,
    )
    if target_index is None:
        return itinerary, f"Không tìm thấy '{target_name}' trong lịch trình hiện tại."

    new_stops = [stop for index, stop in enumerate(itinerary.stops) if index != target_index]
    if target_index < len(new_stops):
        next_stop = new_stops[target_index]
        previous_stop = new_stops[target_index - 1] if target_index else None
        previous_pos = (
            (previous_stop.lat, previous_stop.lon)
            if previous_stop
            else (constraints.origin_lat, constraints.origin_lon)
        )
        previous_leave = (
            _parse_hhmm(previous_stop.leave)
            if previous_stop
            else _parse_hhmm(constraints.free_start)
        )
        depart_at = datetime.combine(target_date, previous_leave)
        route = await _get_route(
            previous_pos,
            (next_stop.lat, next_stop.lon),
            depart_at,
            constraints.travel_mode,
        )
        weather_point, _ = await _weather_at(previous_pos[0], previous_pos[1], depart_at)
        rain_mm_h = weather_point.precipitation_mm if weather_point and weather_point.precipitation_mm is not None else 0.0
        route = traffic_heuristic.apply_weather_traffic_adjustment(route, rain_mm_h)
        travel_minutes = (
            round((route.duration_s or 0) / 60)
            if route.duration_s is not None
            else next_stop.travel_minutes_from_prev
        )
        route_arrive = depart_at + timedelta(minutes=travel_minutes)
        scheduled_arrive = datetime.combine(target_date, _parse_hhmm(next_stop.arrive))
        new_stops[target_index] = next_stop.model_copy(update={
            "travel_minutes_from_prev": travel_minutes,
            "wait_minutes_from_prev": round(max(0, (scheduled_arrive - route_arrive).total_seconds() / 60), 1),
            "route_source": route.source,
            "traffic_aware": route.traffic_aware,
            "route_geometry": route.geometry,
        })

    updated = itinerary.model_copy(update={"stops": new_stops})
    updated.notes = list(updated.notes) + [
        f"Đã xóa '{target_name}'; route tới điểm kế tiếp đã được tính lại."
    ]
    return updated, None


async def apply_mobility_constraint(
    constraints: DayConstraints,
    itinerary: Itinerary,
    target_date: date_type,
) -> Itinerary:
    """Ràng buộc di chuyển mới (vd. đi cùng người lớn tuổi) áp dụng cho TOÀN NGÀY,
    nhưng vẫn giữ các điểm đã được user chốt thủ công (just_changed=True ở lượt trước)."""
    locked = [s for s in itinerary.stops if s.just_changed]
    rebuilt = await build_itinerary(constraints, target_date, keep_stops=locked)
    rebuilt = rebuilt.model_copy(update={
        "sunset": itinerary.sunset,
        "air_quality_aqi": itinerary.air_quality_aqi,
        "air_quality_label": itinerary.air_quality_label,
        "daily_info_source": itinerary.daily_info_source,
    })
    locked_names = {s.name for s in locked}
    for s in rebuilt.stops:
        if s.name not in locked_names:
            s.just_changed = True
    return rebuilt
