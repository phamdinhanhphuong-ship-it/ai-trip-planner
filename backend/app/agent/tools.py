from __future__ import annotations

from datetime import date, datetime

from langchain_core.tools import tool

from app.services import geocode_service, places_service, routing_service, trip_planner, weather_service

NO_DATA_PREFIX = "KHÔNG CÓ DỮ LIỆU"


@tool
async def geocode_place(query: str) -> str:
    """Chuyển tên địa điểm/địa chỉ thành tọa độ (lat, lon). Dùng trước khi tra thời tiết, địa điểm gần đó hoặc tính đường đi."""
    result = await geocode_service.geocode(query)
    if result.error or result.latitude is None:
        return f"{NO_DATA_PREFIX}: {result.error or 'không tìm thấy tọa độ cho ' + query}"
    return (
        f"lat={result.latitude}, lon={result.longitude}, "
        f"tên đầy đủ='{result.display_name}' (nguồn: {result.source.title()})"
    )


@tool
async def get_weather_hourly(lat: float, lon: float, date_str: str, hour: int) -> str:
    """Lấy dự báo thời tiết theo giờ tại 1 tọa độ. date_str dạng YYYY-MM-DD, hour từ 0-23."""
    try:
        target_date = date.fromisoformat(date_str)
    except ValueError:
        return f"{NO_DATA_PREFIX}: date_str '{date_str}' không đúng định dạng YYYY-MM-DD"
    if not 0 <= hour <= 23:
        return f"{NO_DATA_PREFIX}: hour phải nằm trong khoảng 0-23"

    result = await weather_service.get_hourly_weather(lat, lon, target_date)
    if result.error:
        return f"{NO_DATA_PREFIX}: {result.error}"

    point = weather_service.get_weather_at_hour(result, datetime(target_date.year, target_date.month, target_date.day, hour))
    if point is None:
        return f"{NO_DATA_PREFIX}: không có dữ liệu cho giờ {hour}:00 ngày {date_str}"

    return (
        f"Lúc {hour}:00 {date_str}: nhiệt độ {point.temperature_c}°C, "
        f"xác suất mưa {point.precipitation_probability_pct}%, lượng mưa {point.precipitation_mm}mm, "
        f"gió {point.wind_speed_kmh}km/h, mã thời tiết {point.weather_code} (nguồn: Open-Meteo)"
    )


@tool
async def search_nearby_places(lat: float, lon: float, radius_m: int, categories_csv: str, depart_hour: int | None = None) -> str:
    """Tìm địa điểm/quán ăn quanh 1 tọa độ theo sở thích và khoảng cách.

    categories_csv: danh sách category cách nhau bởi dấu phẩy, ví dụ 'restaurant,museum,cafe'.
    depart_hour: nếu cung cấp (0-23), sẽ lọc thêm quán đang mở cửa tại giờ đó (dùng ngày hiện tại).
    """
    categories = [c.strip() for c in categories_csv.split(",") if c.strip()]
    result = await places_service.search_places(lat, lon, radius_m, categories)
    if result.error and not result.places:
        return f"{NO_DATA_PREFIX}: {result.error}"

    places = result.places
    lines = [f"Tìm thấy {len(places)} địa điểm (nguồn: {', '.join(result.sources_used)}):"]

    if depart_hour is not None:
        when = datetime.now().replace(hour=depart_hour, minute=0, second=0, microsecond=0)
        open_places, unknown_places = places_service.filter_open_at(places, when)
        lines.append(f"- Đang mở lúc {depart_hour}:00: {len(open_places)} địa điểm")
        for p in open_places[:10]:
            lines.append(f"  * {p.name} ({p.category}), cách {round(p.distance_m or 0)}m")
        if unknown_places:
            lines.append(
                f"- {len(unknown_places)} địa điểm KHÔNG RÕ giờ mở cửa (nguồn không có thông tin) — "
                "không được suy đoán là đang mở hay đóng:"
            )
            for p in unknown_places[:10]:
                lines.append(f"  * {p.name} ({p.category}), cách {round(p.distance_m or 0)}m")
        return "\n".join(lines)

    for p in places[:15]:
        opening_note = p.opening_hours_raw if p.opening_hours_known else "không rõ giờ mở cửa"
        lines.append(f"  * {p.name} ({p.category}), cách {round(p.distance_m or 0)}m, giờ mở cửa: {opening_note}")
    return "\n".join(lines)


@tool
async def get_route_between(origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float, depart_at_iso: str, travel_mode: str = "car") -> str:
    """Tính route theo giờ và phương tiện: car, motorcycle hoặc walking."""
    try:
        depart_at = datetime.fromisoformat(depart_at_iso)
    except ValueError:
        return f"{NO_DATA_PREFIX}: depart_at_iso '{depart_at_iso}' không đúng định dạng ISO 8601"

    if travel_mode not in {"car", "motorcycle", "walking"}:
        return f"{NO_DATA_PREFIX}: travel_mode phải là car, motorcycle hoặc walking"
    if travel_mode == "car":
        result = await routing_service.get_route((origin_lat, origin_lon), (dest_lat, dest_lon), depart_at)
    else:
        result = await routing_service.get_route(
            (origin_lat, origin_lon), (dest_lat, dest_lon), depart_at, travel_mode
        )
    if result.error and result.source == "none":
        return f"{NO_DATA_PREFIX}: {result.error}"

    minutes = round((result.duration_s or 0) / 60)
    km = round((result.distance_m or 0) / 1000, 1)
    label = routing_service.describe_source(result)
    warning = f"\nLưu ý: {result.error}" if result.error else ""
    return f"Khoảng cách {km}km, thời gian di chuyển ước tính {minutes} phút — {label}.{warning}"


@tool
async def compare_route_departure_times(
    origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float, depart_times_csv: str
) -> str:
    """So sánh thời gian di chuyển A->B tại nhiều mốc giờ khởi hành khác nhau.

    depart_times_csv: các mốc giờ ISO 8601 cách nhau bởi dấu phẩy, ví dụ '2026-09-21T18:00:00,2026-09-21T20:00:00'.
    """
    try:
        depart_times = [datetime.fromisoformat(s.strip()) for s in depart_times_csv.split(",") if s.strip()]
    except ValueError:
        return f"{NO_DATA_PREFIX}: depart_times_csv chứa giá trị không đúng định dạng ISO 8601"

    if not depart_times:
        return f"{NO_DATA_PREFIX}: cần ít nhất 1 mốc giờ khởi hành"

    results = await routing_service.compare_departure_times((origin_lat, origin_lon), (dest_lat, dest_lon), depart_times)
    lines = []
    for r in results:
        minutes = round((r.duration_s or 0) / 60) if r.duration_s is not None else None
        km = round((r.distance_m or 0) / 1000, 1) if r.distance_m is not None else None
        label = routing_service.describe_source(r)
        if minutes is None:
            lines.append(f"- {r.depart_at.strftime('%H:%M')}: {NO_DATA_PREFIX} ({r.error})")
        else:
            note = f" [{r.error}]" if r.error else ""
            lines.append(f"- {r.depart_at.strftime('%H:%M')}: {km}km, {minutes} phút — {label}{note}")
    return "\n".join(lines)


@tool
async def plan_nearby_visit(
    lat: float, lon: float, categories_csv: str, max_distance_m: float, depart_at_iso: str
) -> str:
    """Lọc địa điểm quanh 1 tọa độ theo sở thích + khoảng cách + giờ mở cửa tại thời điểm khởi hành.

    Đây là tool LỌC CHÍNH THỨC (bắt buộc dùng thay vì tự suy luận) — luôn dùng tool này khi
    người dùng cần gợi ý địa điểm/quán ăn kèm điều kiện khoảng cách hoặc giờ đi.
    categories_csv: category cách nhau bởi dấu phẩy, ví dụ 'restaurant,cafe'.
    depart_at_iso: thời điểm dự kiến đến, dạng ISO 8601, ví dụ '2026-09-21T18:00:00'.
    """
    try:
        when = datetime.fromisoformat(depart_at_iso)
    except ValueError:
        return f"{NO_DATA_PREFIX}: depart_at_iso '{depart_at_iso}' không đúng định dạng ISO 8601"

    categories = [c.strip() for c in categories_csv.split(",") if c.strip()]
    result = await trip_planner.plan_nearby_visit((lat, lon), categories, max_distance_m, when)

    if result.error and not result.matched and not result.unknown_opening_hours:
        return f"{NO_DATA_PREFIX}: {result.error}"

    lines = [f"Nguồn dữ liệu: {', '.join(result.sources_used)}. Thời tiết: {result.weather_note}"]
    lines.append(
        f"Đang mở & trong bán kính {max_distance_m}m: {len(result.matched)} địa điểm "
        f"(đã loại {result.excluded_too_far} địa điểm quá xa, {result.excluded_closed} địa điểm đang đóng cửa)"
    )
    for item in result.matched[:10]:
        p = item.place
        lines.append(f"  * {p.name} ({p.category}), cách {round(p.distance_m or 0)}m, giờ: {p.opening_hours_raw}")

    if result.unknown_opening_hours:
        lines.append(f"KHÔNG RÕ giờ mở cửa (không được suy đoán đang mở hay đóng): {len(result.unknown_opening_hours)} địa điểm")
        for item in result.unknown_opening_hours[:10]:
            p = item.place
            lines.append(f"  * {p.name} ({p.category}), cách {round(p.distance_m or 0)}m")

    return "\n".join(lines)


TOOLS = [
    geocode_place,
    get_weather_hourly,
    search_nearby_places,
    plan_nearby_visit,
    get_route_between,
    compare_route_departure_times,
]
