from __future__ import annotations

import os
import json
import uuid

import httpx
import streamlit as st
import streamlit.components.v1 as components

BACKEND_URL = os.environ.get("GUIDEPASS_BACKEND_URL", "http://127.0.0.1:8000")

SAMPLE_PROMPTS = [
    ("1. Lịch trình cả ngày", "Mình ở khách sạn gần chợ Bến Thành, thứ Bảy này rảnh từ 8h đến 20h. Thích lịch sử và đồ ăn đường phố, ngân sách khoảng 500k/người."),
    ("2. Đổi buổi chiều (mưa)", "Dự báo chiều mưa to, đổi giúp mình phần buổi chiều."),
    ("3. Đổi bữa trưa", "Bữa trưa đổi sang quán chay gần đó nha."),
    ("4. Đi cùng người lớn tuổi", "À mình đi với ông bà 70 tuổi, hạn chế đi bộ."),
    ("5. So sánh giờ khởi hành", "18h tối nay đi từ Quận 1 lên Landmark 81 mất bao lâu? Đi lúc 20h thì sao?"),
    ("6. Kiểm tra không bịa", "Cho mình quán phở mở cửa lúc 2h sáng ở Quận 5."),
]

st.set_page_config(page_title="GuidePass", page_icon="G", layout="wide", initial_sidebar_state="expanded")

st.markdown(
    """
    <style>
    :root { --ink: #242424; --forest: #d9d9d9; --lime: #5f6368; --paper: #f7f5ee; --coral: #e9694e; }
      .stApp { background: var(--paper); color: var(--ink); }
    [data-testid="stSidebar"] { background: #dedede; }
    [data-testid="stSidebar"] * { color: #242424; }
            [data-testid="stSidebar"] code {
                background: #c8c8c8 !important;
                color: #1f1f1f !important;
                border: 1px solid #b2b2b2;
                border-radius: 4px;
                padding: 0.15rem 0.35rem;
            }
            [data-testid="stSidebar"] hr {
                border-color: var(--coral) !important;
                opacity: 0.75;
            }
      header[data-testid="stHeader"] { background: var(--paper); }
      .block-container { max-width: 1180px; padding-top: 4.5rem; }
    .brand { font-size: .78rem; font-weight: 800; letter-spacing: .13em; color: var(--coral); text-transform: uppercase; }
      .hero-title { font-size: 2.4rem; font-weight: 800; line-height: 1.05; color: var(--ink); margin: .25rem 0 .5rem; }
      .hero-copy { color: #49635e; font-size: 1rem; max-width: 760px; margin-bottom: 1.2rem; }
    div.stButton > button { background: #cfcfcf; color: #242424; border: 1px solid #b8b8b8; border-radius: 5px; font-weight: 700; }
    div.stButton > button:hover { background: #bdbdbd; color: #111111; }
      [data-testid="stChatMessage"] { background: #ffffff; border: 1px solid #d9ddd2; border-radius: 8px; }
      [data-testid="stChatMessage"] p,
      [data-testid="stChatMessage"] li,
      [data-testid="stChatMessage"] span { color: var(--ink) !important; }
    [data-testid="stMarkdownContainer"] h1,
    [data-testid="stMarkdownContainer"] h2,
    [data-testid="stMarkdownContainer"] h3 { color: var(--ink) !important; }
    [data-testid="stAlert"] p,
    [data-testid="stAlert"] span { color: var(--ink) !important; }
      [data-testid="stBaseButton-headerNoPadding"],
      [data-testid="stBaseButton-headerNoPadding"] svg { color: var(--ink) !important; }
    </style>
    """,
    unsafe_allow_html=True,
)

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if "messages" not in st.session_state:
    st.session_state.messages = []
if "itinerary" not in st.session_state:
    st.session_state.itinerary = None


def _new_session() -> None:
    st.session_state.session_id = str(uuid.uuid4())
    st.session_state.messages = []
    st.session_state.itinerary = None


def send_message(text: str) -> None:
    st.session_state.messages.append({"role": "user", "content": text})
    try:
        response = httpx.post(
            f"{BACKEND_URL}/chat",
            json={"session_id": st.session_state.session_id, "message": text},
            timeout=90.0,
        )
        response.raise_for_status()
        data = response.json()
        reply = data.get("reply", "")
        st.session_state.itinerary = data.get("itinerary")
    except httpx.HTTPError as exc:
        reply = (
            f"KHÔNG KẾT NỐI ĐƯỢC BACKEND ({exc}). "
            "Hãy chạy `uvicorn app.main:app --reload` trong thư mục backend trước."
        )
    st.session_state.messages.append({"role": "assistant", "content": reply})


def _render_leaflet_map(itinerary: dict) -> None:
    stops = itinerary.get("stops", [])
    if not stops:
        return
    map_stops = [
        {
            "name": stop.get("name", ""),
            "category": stop.get("category", ""),
            "arrive": stop.get("arrive", ""),
            "leave": stop.get("leave", ""),
            "lat": stop.get("lat"),
            "lon": stop.get("lon"),
            "source": stop.get("route_source") or "unknown",
            "traffic": bool(stop.get("traffic_aware")),
            "wiki": stop.get("wikipedia_url") or "",
            "geometry": stop.get("route_geometry") or [],
        }
        for stop in stops
        if stop.get("lat") is not None and stop.get("lon") is not None
    ]
    stops_json = json.dumps(map_stops, ensure_ascii=False).replace("</", "<\\/")
    components.html(
        f"""
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
        <div id="guidepass-map" style="height: 500px; width: 100%; border-radius: 8px;"></div>
        <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
        <script>
        const stops = {stops_json};
        const map = L.map('guidepass-map');
        L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
            maxZoom: 19,
            attribution: '&copy; OpenStreetMap contributors'
        }}).addTo(map);
        const bounds = [];
        const escapeHtml = (value) => String(value).replace(/[&<>'\"]/g, (char) => ({{
            '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
        }}[char]));
        stops.forEach((stop, index) => {{
            const point = [stop.lat, stop.lon];
            bounds.push(point);
            const marker = L.marker(point).addTo(map);
            const wiki = stop.wiki ? `<br><a href="${{escapeHtml(stop.wiki)}}" target="_blank">Wikipedia</a>` : '';
            marker.bindPopup(`<strong>${{index + 1}}. ${{escapeHtml(stop.name)}}</strong><br>${{escapeHtml(stop.arrive)}}-${{escapeHtml(stop.leave)}}<br>${{escapeHtml(stop.category)}}<br>Route: ${{escapeHtml(stop.source)}} (${{stop.traffic ? 'traffic thật' : 'ước lượng tĩnh'}})${{wiki}}`);
            let geometry = stop.geometry;
            if (!geometry || geometry.length < 2) {{
                geometry = index === 0 ? [] : [
                    [stops[index - 1].lat, stops[index - 1].lon], point
                ];
            }}
            if (geometry.length >= 2) {{
                const realGeometry = stop.geometry && stop.geometry.length >= 2;
                L.polyline(geometry, {{
                    color: stop.traffic ? '#d94841' : '#4567a8',
                    weight: 5,
                    opacity: 0.78,
                    dashArray: realGeometry ? null : '8 8'
                }}).addTo(map);
            }}
        }});
        if (bounds.length) {{ map.fitBounds(bounds, {{padding: [24, 24]}}); }}
        </script>
        """,
        height=530,
        scrolling=False,
    )


with st.sidebar:
    st.markdown("### GuidePass")
    st.caption("Trợ lý lập lịch trình TP.HCM — hội thoại nhiều lượt")
    st.divider()
    st.caption(f"Session: `{st.session_state.session_id[:8]}`")
    if st.button("Bắt đầu phiên mới", use_container_width=True):
        _new_session()
        st.rerun()
    st.divider()
    st.markdown("**Kịch bản mẫu**")
    for label, prompt in SAMPLE_PROMPTS:
        if st.button(label, use_container_width=True, key=f"sample-{label}"):
            send_message(prompt)
            st.rerun()
    st.divider()
    st.markdown("**Nguyên tắc dữ liệu của tôi**")
    st.caption("Không bịa địa điểm/giờ mở cửa/thời tiết/giao thông. Khi thiếu dữ liệu thật, hệ thống nói rõ thay vì đoán.")

st.markdown('<div class="brand">Trip intelligence · TP.HCM</div>', unsafe_allow_html=True)
st.markdown('<div class="hero-title">Lịch trình theo giờ, đúng thời tiết &amp; giao thông.</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="hero-copy">Nói cho GuidePass biết bạn đang ở đâu, rảnh lúc nào và thích gì. '
    "Sửa từng phần (buổi chiều, một điểm, ràng buộc di chuyển) mà không cần lập lại từ đầu.</div>",
    unsafe_allow_html=True,
)

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])


def _render_itinerary(itinerary: dict) -> None:
    if itinerary.get("budget_vnd_per_person"):
        st.info(
            f"Ngân sách: {itinerary['budget_vnd_per_person']:,} VNĐ/người | "
            "Tổng chi phí: chưa có dữ liệu giá thật từ provider."
        )
    daily_info = []
    if itinerary.get("sunset"):
        daily_info.append(f"Hoàng hôn: {itinerary['sunset']}")
    if itinerary.get("air_quality_aqi") is not None:
        label = itinerary.get("air_quality_label") or "chưa phân loại"
        daily_info.append(f"AQI: {itinerary['air_quality_aqi']} ({label})")
    if daily_info:
        st.info("Thông tin trong ngày | " + " | ".join(daily_info))
    if itinerary.get("stops"):
        st.subheader("Bản đồ lộ trình")
        _render_leaflet_map(itinerary)
    stops = itinerary.get("stops", [])
    if not stops:
        st.info("Lịch trình hiện chưa có điểm nào (có thể do thiếu dữ liệu thật — xem ghi chú bên dưới).")
    else:
        rows = []
        for s in stops:
            name = s["name"]
            if s.get("just_changed"):
                name = f"🆕 {name}"
            rows.append(
                {
                    "Giờ đến": s["arrive"],
                    "Giờ đi": s["leave"],
                    "Điểm": name,
                    "Loại": s["category"],
                    "Trong nhà": "Có" if s["indoor"] else "Không",
                    "Di chuyển (phút)": s["travel_minutes_from_prev"],
                    "Chờ đến khung (phút)": s.get("wait_minutes_from_prev", 0),
                    "Thời tiết": s["weather_note"],
                    "Lý do": s["reason"],
                    "Nguồn": s["source"],
                    "Wikipedia": s.get("wikipedia_url") or "",
                }
            )
        st.dataframe(rows, use_container_width=True, hide_index=True)

    if itinerary.get("unfilled_slots"):
        st.warning(f"{itinerary['unfilled_slots']} khung giờ không tìm được địa điểm thật phù hợp — để trống, không bịa thêm.")
    for note in itinerary.get("notes", []):
        st.caption(f"Lưu ý: {note}")
    with st.expander("Xem JSON schema thô (Pydantic)"):
        st.json(itinerary)


if st.session_state.itinerary:
    st.subheader("Lịch trình hiện tại")
    _render_itinerary(st.session_state.itinerary)

user_input = st.chat_input("Nhắn cho GuidePass...")
if user_input:
    with st.chat_message("user"):
        st.markdown(user_input)
    with st.chat_message("assistant"):
        with st.spinner("Đang xử lý..."):
            send_message(user_input)
            st.markdown(st.session_state.messages[-1]["content"])
    st.rerun()
