# GuidePass AI Trip Planner

## 0. Giới thiệu

GuidePass AI là trợ lý lập lịch trình du lịch bằng tiếng Việt cho TP.HCM. Người dùng có thể trò chuyện nhiều lượt để:

- Lập lịch trình theo vị trí, thời gian rảnh, sở thích và ngân sách.
- Chọn địa điểm thật từ các API POI, không tự bịa địa điểm.
- Né mưa và nắng gắt bằng dữ liệu thời tiết theo giờ.
- Ước lượng thời gian di chuyển theo giờ cao điểm và lượng mưa.
- Sửa buổi chiều, đổi bữa ăn, thêm/xóa/đổi một điểm mà không dựng lại vô căn cứ toàn bộ ngày.
- Xem Wikipedia, AQI, giờ hoàng hôn và bản đồ Leaflet cùng lịch trình.

Output mỗi lượt gồm cả câu trả lời tự nhiên và JSON itinerary Pydantic.

## Kiến trúc tổng quan

```text
Streamlit chat UI
        |
        v
FastAPI POST /chat
        |
        +-- chat_handler: nhận diện intent + session state
        +-- itinerary_builder: xếp lịch, kiểm tra rule, patch cục bộ
        +-- LangGraph + Groq: xử lý câu hỏi mở và gọi tools
        |
        +-- Goong / Geoapify: geocode và POI
        +-- Open-Meteo: weather, sunset, AQI
        +-- TomTom / OSRM / Goong / Mapbox: routing
        +-- Wikipedia tiếng Việt: mô tả địa danh
```

## Tech stack & API

- Python 3.11+; FastAPI; Streamlit.
- LangChain + LangGraph để điều phối tool calling.
- Groq Cloud với model `openai/gpt-oss-120b` cho câu hỏi mở.
- `diskcache` cho cache file-based và quota counter, không cần Docker/Redis.
- Goong geocoding/Places/Direction: provider chính cho dữ liệu Việt Nam.
- Geoapify geocoding/Places: fallback cho Goong.
- Open-Meteo Forecast: thời tiết theo giờ và giờ hoàng hôn.
- Open-Meteo Air Quality: chỉ số US AQI theo giờ.
- TomTom Routing: route ô tô có traffic thật khi API key/quota khả dụng.
- OSRM: fallback route tĩnh không có traffic thật.
- Mapbox Directions: route đi bộ khi có key.
- Wikipedia REST API: bổ sung mô tả và link địa danh.
- Leaflet CDN: bản đồ marker, popup và route geometry trong Streamlit.

Qdrant/RAG và Ollama không được sử dụng trong phiên bản này. Wikipedia hiện là enrichment trực tiếp, không phải hệ thống RAG.

## Setup & installation

Yêu cầu Python 3.11 trở lên.

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Mở `backend/.env` và điền key tương ứng:

```text
GROQ_API_KEY=...
TOMTOM_API_KEY=...
GOONG_API_KEY=...
GEOAPIFY_API_KEY=...
MAPBOX_API_KEY=...
```

Không commit `.env` hoặc API key thật. Chỉ commit `.env.example` với giá trị rỗng.

## How to run

Mở terminal thứ nhất để chạy backend:

```powershell
cd D:\guidepass-ai-test
D:\guidepass-ai-test\backend\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir D:\guidepass-ai-test\backend --reload
```

Kiểm tra backend tại <http://127.0.0.1:8000/health>.

Mở terminal thứ hai để chạy Streamlit:

```powershell
cd D:\guidepass-ai-test
D:\guidepass-ai-test\backend\.venv\Scripts\python.exe -m streamlit run D:\guidepass-ai-test\frontend\streamlit_app.py
```

Mở <http://127.0.0.1:8501>. Có thể đổi backend URL bằng `GUIDEPASS_BACKEND_URL`.

## API chính

```http
POST /chat
Content-Type: application/json
```

```json
{
  "session_id": "demo-session",
  "message": "Lập lịch từ 8h đến 20h quanh Chợ Bến Thành"
}
```

Response gồm:

```json
{
  "session_id": "demo-session",
  "reply": "...câu trả lời tự nhiên...",
  "itinerary": {"date": "2026-09-24", "stops": []}
}
```

`session_id` được dùng để giữ itinerary và constraints trong cùng một phiên chat.

## Logic thời tiết & giao thông

### Hệ số tăng thời gian di chuyển

Khi route không có traffic thật, `traffic_heuristic.py` dùng:

- Khung cao điểm `07:00-09:00` và `16:30-19:00`: nhân `1.6`.
- Mưa từ `2 mm/h` trở lên: nhân thêm `1.3`.
- Nếu đồng thời cao điểm và mưa: hệ số cộng dồn `1.6 x 1.3 = 2.08`.

Các hệ số bám theo công thức bắt buộc của đề bài. Khi có TomTom traffic thật, không nhân lại hệ số cao điểm vì TomTom đã tính traffic theo `departAt`; hệ số mưa vẫn được cộng thêm theo rule của bài.

### Heuristic traffic

OSRM chỉ trả route tĩnh, không biết tình trạng kẹt xe hiện tại. Vì vậy hệ thống:

1. Gọi TomTom trước nếu có key và quota.
2. Nếu TomTom lỗi/hết quota, fallback sang OSRM.
3. Áp dụng heuristic theo giờ cao điểm và mưa cho route OSRM.
4. Luôn ghi rõ `TomTom traffic thực` hoặc `OSRM ước lượng tĩnh` trong response.

## Dữ liệu thật và dữ liệu mock

### Dữ liệu thật trong runtime

- Địa điểm/tọa độ: Goong hoặc Geoapify.
- Thời tiết: Open-Meteo.
- AQI: Open-Meteo Air Quality.
- Sunset: Open-Meteo Forecast.
- Route: TomTom, OSRM, Goong hoặc Mapbox tùy phương tiện/provider.
- Wikipedia: Wikipedia REST API.

Runtime không tự tạo nhà hàng, tọa độ, giờ mở cửa, thời tiết hoặc thời gian route. Khi provider không có dữ liệu, hệ thống trả `KHÔNG CÓ DỮ LIỆU` hoặc để trống khung giờ thay vì bịa.

### Dữ liệu mock trong test

Các file trong `backend/tests/` mock response HTTP để test ổn định, không tốn quota:

- Mock địa điểm, opening hours, geocode.
- Mock weather và AQI/sunset.
- Mock TomTom, OSRM, Goong, Mapbox routing.
- Mock Wikipedia.
- Mock Groq agent trong test scenario 5/6.

Mock chỉ dùng trong test, không được dùng trong runtime demo.

## Domain logic của itinerary

- Bữa sáng, trưa, tối mỗi loại tối đa một lần/ngày.
- Khung rảnh phủ breakfast/lunch/dinner nhưng không tìm được dữ liệu sẽ ghi note rõ ràng.
- Khoảng cách hai bữa chính được kiểm tra trong khoảng khuyến nghị `150-360 phút`.
- Không xếp hai category giống nhau liên tiếp nếu không có lý do hợp lệ.
- Nếu điểm ăn uống vượt khoảng 60% itinerary, hệ thống ghi cảnh báo và ưu tiên điểm tham quan.
- Địa điểm phải nằm trong khung giờ rảnh và kiểm tra opening hours sau khi tính route.
- Mưa hoặc cảm nhận nóng từ `34°C` trở lên sẽ ưu tiên điểm trong nhà.
- Ngân sách được lưu và hiển thị; vì provider hiện không có giá đáng tin cậy nên không tự tính tổng chi phí.

## Sửa lịch nhiều lượt

Các request rõ mục tiêu được xử lý cục bộ:

- Đổi phần buổi chiều: giữ nguyên buổi sáng.
- Đổi bữa trưa/tối: patch đúng stop tương ứng.
- Thêm điểm: cần category và khung giờ, lấy candidate thật từ provider.
- Xóa điểm: cần đúng tên stop, sau đó tính lại route tới điểm kế tiếp.
- Đổi một stop: kiểm tra overlap và cập nhật route metadata.

Nếu người dùng nói mơ hồ như “xóa một điểm” hoặc “thêm một địa điểm”, AI hỏi lại thay vì tự đoán.

## Schema chính

Mỗi `Stop` có các field bắt buộc của đề:

```text
name, category, indoor, lat, lon, arrive, leave,
travel_minutes_from_prev, weather_note, reason, source
```

Field bổ sung:

```text
wait_minutes_from_prev, meal_type, route_geometry,
route_source, traffic_aware, wikipedia_url,
wikipedia_description, just_changed
```

`Itinerary` bổ sung ngân sách, `sunset`, AQI, nguồn daily info, notes và số slot bỏ trống.

## Bonus features

- Wikipedia enrichment cho địa danh có bài viết.
- AQI và sunset theo ngày.
- Leaflet map với marker/popup và route geometry.
- Cache provider và quota guard cho TomTom.
- Opening-hours filter, weather-aware indoor selection.
- Meal logic validation và cảnh báo itinerary thiếu đa dạng.
- Patch add/edit/delete cục bộ, giữ session state.
- Route source labeling: traffic thật vs ước lượng tĩnh.

## Kiểm thử

Chạy từ thư mục project root:

```powershell
D:\guidepass-ai-test\backend\.venv\Scripts\python.exe -m pytest -q
D:\guidepass-ai-test\backend\.venv\Scripts\python.exe -m compileall -q backend\app backend\tests frontend\streamlit_app.py
D:\guidepass-ai-test\backend\.venv\Scripts\python.exe -m pip check
```

Bộ test bao phủ service, routing, weather, Wikipedia, meal validation, session chat, API validation và các kịch bản chỉnh itinerary. Các scenario phụ thuộc Groq thật cần được smoke-test thêm khi API key/quota khả dụng.

## Giới hạn đã biết

- Session hiện lưu in-memory, phù hợp demo một process; restart server sẽ mất session.
- Không có dữ liệu giá đáng tin cậy từ provider nên budget chưa thể tính thành tổng tiền thật.
- Nếu provider POI không có key hoặc bị chặn mạng, itinerary có thể có slot bỏ trống.
- Leaflet dùng tile/CDN bên ngoài; môi trường không có Internet sẽ không tải được nền bản đồ.
- Nominatim/Overpass được giữ trong code/test nhưng không phải provider runtime mặc định.

## Cấu trúc thư mục

```text
backend/
  app/
    main.py              FastAPI entrypoint: /health, /chat
    config.py            Settings từ backend/.env
    api/                 HTTP routes
    agent/               chat handler, LangGraph agent, tools, prompts
    core/                cache, quota, rate limit, logging, session store
    models/              Pydantic schemas
    services/            weather, places, Wikipedia, routing, itinerary builder
  tests/                 Unit và integration tests
frontend/
  streamlit_app.py       Multi-turn chat UI + Leaflet map
```
