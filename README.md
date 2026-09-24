# GuidePass AI Trip Planner

Trợ lý lập lịch trình du lịch bằng tiếng Việt cho TP.HCM — trò chuyện nhiều lượt, gợi ý địa điểm **thật** (không bịa), né mưa/nắng bằng dự báo theo giờ, và ước lượng thời gian di chuyển theo giờ cao điểm.

> Bài làm cho đề take-home **Thực tập sinh AI Engineer – GuidePass AI (09/2026)**.

---

## 1. Tóm tắt nhanh

| | |
|---|---|
| **Ngôn ngữ** | Python 3.11+ |
| **Backend** | FastAPI |
| **Agent** | LangChain + LangGraph, LLM qua Groq (`openai/gpt-oss-120b`) |
| **Frontend** | Streamlit (chat + bản đồ Leaflet) |
| **Địa điểm** | Goong (chính) → Geoapify (fallback) |
| **Thời tiết** | Open-Meteo (dự báo theo giờ, AQI, giờ hoàng hôn) |
| **Giao thông** | TomTom (traffic thật) → OSRM (fallback, dùng heuristic) |
| **Cache** | `diskcache` (file-based, không cần Docker/Redis) |

Ghi chú: Qdrant/RAG và Ollama nêu trong đề bài **không** được dùng ở phiên bản này — thay vào đó Wikipedia REST API được gọi trực tiếp để làm giàu mô tả địa điểm (enrichment, không phải RAG).

---

## 2. Tính năng chính

- Lập lịch trình cả ngày từ vị trí, khung giờ rảnh, sở thích, ngân sách người dùng cung cấp.
- Chỉ dùng địa điểm thật lấy từ API (Goong/Geoapify); khi không có dữ liệu, hệ thống báo `KHÔNG CÓ DỮ LIỆU` thay vì bịa.
- Né giờ mưa và giờ nắng gắt (≥34°C) bằng dự báo thời tiết theo giờ, ưu tiên điểm trong nhà khi cần.
- Thời gian di chuyển tính theo giờ khởi hành: giờ cao điểm và lượng mưa làm tăng thời gian (hệ số nhân dồn), route traffic thật từ TomTom khi có, fallback OSRM + heuristic khi không.
- Sửa lịch cục bộ theo từng lượt chat: đổi buổi chiều, đổi một bữa, thêm/xóa/đổi một điểm — không dựng lại toàn bộ ngày, hỏi lại nếu người dùng nói mơ hồ.
- Kiểm tra ràng buộc: không chồng giờ, đúng giờ mở cửa, mỗi bữa sáng/trưa/tối tối đa 1 lần/ngày, khoảng cách giữa 2 bữa chính 150–360 phút.
- Trả về đồng thời câu trả lời tự nhiên và JSON itinerary theo schema Pydantic.
- Bonus: Wikipedia enrichment, AQI + giờ hoàng hôn, bản đồ Leaflet, cache + quota guard cho TomTom, gắn nhãn rõ "traffic thật" vs "ước lượng tĩnh".

### Đối chiếu với 6 kịch bản test trong đề bài

| # | Kịch bản | Xử lý |
|---|---|---|
| 1 | Lịch cả ngày từ yêu cầu ban đầu | `itinerary_builder` dựng lịch đầy đủ từ POI + weather + traffic |
| 2 | Đổi buổi chiều vì mưa to | Patch cục bộ, giữ nguyên buổi sáng |
| 3 | Đổi bữa trưa sang quán chay gần đó | Patch đúng 1 stop, giữ nguyên ràng buộc cũ |
| 4 | Thêm ràng buộc đi với người già, hạn chế đi bộ | Rebuild có ràng buộc mới: ít điểm hơn, ưu tiên xe, có chỗ nghỉ |
| 5 | So sánh thời gian di chuyển ở 2 giờ khởi hành | Gọi routing 2 lần, ghi rõ nguồn (TomTom thật / OSRM ước lượng) |
| 6 | Hỏi dữ liệu không tồn tại (phở 2h sáng Q5) | Trả lời trung thực "không có dữ liệu", không bịa quán |

---

## 3. Kiến trúc hệ thống

```text
Streamlit chat UI
        |
        v
FastAPI  POST /chat
        |
        +-- chat_handler        nhận diện intent + session state
        +-- itinerary_builder   xếp lịch, kiểm tra rule, patch cục bộ
        +-- LangGraph + Groq    xử lý câu hỏi mở, gọi tools
        |
        +-- Goong / Geoapify    geocode + POI
        +-- Open-Meteo          weather, sunset, AQI
        +-- TomTom / OSRM       routing (traffic thật / ước lượng)
        +-- Wikipedia (vi)      mô tả địa danh
```

### Cấu trúc thư mục

```text
backend/
  app/
    main.py       FastAPI entrypoint: /health, /chat
    config.py     Settings từ backend/.env
    api/          HTTP routes
    agent/        chat handler, LangGraph agent, tools, prompts
    core/         cache, quota, rate limit, logging, session store
    models/       Pydantic schemas
    services/     weather, places, Wikipedia, routing, itinerary builder
  tests/          Unit và integration tests
frontend/
  streamlit_app.py   Multi-turn chat UI + Leaflet map
```

---

## 4. Cách chạy

Yêu cầu Python 3.11+.

**Cài đặt**

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Điền key vào `backend/.env`:

```text
GROQ_API_KEY=...
TOMTOM_API_KEY=...
GOONG_API_KEY=...
GEOAPIFY_API_KEY=...
MAPBOX_API_KEY=...
```

**Chạy backend** (terminal 1):

```powershell
cd D:\guidepass-ai-test
D:\guidepass-ai-test\backend\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir D:\guidepass-ai-test\backend --reload
```

Kiểm tra: <http://127.0.0.1:8000/health>

**Chạy frontend** (terminal 2):

```powershell
cd D:\guidepass-ai-test
D:\guidepass-ai-test\backend\.venv\Scripts\python.exe -m streamlit run D:\guidepass-ai-test\frontend\streamlit_app.py
```

Mở: <http://127.0.0.1:8501> (đổi backend URL bằng biến môi trường `GUIDEPASS_BACKEND_URL`)

---

## 5. API chính

```http
POST /chat
Content-Type: application/json
```

Request:

```json
{
  "session_id": "demo-session",
  "message": "Lập lịch từ 8h đến 20h quanh Chợ Bến Thành"
}
```

Response:

```json
{
  "session_id": "demo-session",
  "reply": "...câu trả lời tự nhiên...",
  "itinerary": { "date": "2026-09-24", "stops": [] }
}
```

`session_id` dùng để giữ itinerary và ràng buộc xuyên suốt một phiên chat.

---

## 6. Logic thời tiết & giao thông

**Hệ số tăng thời gian di chuyển** (`traffic_heuristic.py`), áp dụng khi route không có traffic thật:

- Giờ cao điểm (`07:00–09:00`, `16:30–19:00`): nhân **1.6**
- Mưa ≥ 2 mm/h: nhân thêm **1.3**
- Cả hai cùng lúc: cộng dồn **1.6 × 1.3 = 2.08**

**Chọn nguồn traffic:**

1. Gọi TomTom trước nếu còn key/quota (traffic thật theo `departAt`).
2. Lỗi hoặc hết quota → fallback OSRM (route tĩnh).
3. Với route OSRM, áp thêm heuristic giờ cao điểm + mưa ở trên.
4. Response luôn ghi rõ nguồn: `TomTom traffic thực` hoặc `OSRM ước lượng tĩnh`.

Khi TomTom trả traffic thật, hệ thống **không** nhân thêm hệ số cao điểm (đã tính trong `departAt`), chỉ cộng thêm hệ số mưa theo rule của đề.

---

## 7. Logic nghiệp vụ của itinerary

- Mỗi bữa sáng/trưa/tối tối đa 1 lần/ngày; khoảng cách giữa 2 bữa chính trong khoảng 150–360 phút.
- Không xếp 2 category giống nhau liên tiếp nếu không có lý do hợp lệ.
- Nếu điểm ăn uống chiếm > 60% itinerary → cảnh báo, ưu tiên điểm tham quan.
- Mỗi điểm phải nằm trong khung giờ rảnh và đúng giờ mở cửa (kiểm tra sau khi tính route).
- Mưa hoặc cảm nhận nóng ≥ 34°C → ưu tiên điểm trong nhà.
- Ngân sách được lưu và hiển thị nhưng không tự tính tổng chi phí (provider hiện chưa có giá đáng tin cậy).

**Sửa lịch nhiều lượt** — xử lý cục bộ, không dựng lại cả ngày:

| Yêu cầu | Xử lý |
|---|---|
| Đổi buổi chiều | Giữ nguyên buổi sáng |
| Đổi bữa trưa/tối | Patch đúng stop tương ứng |
| Thêm điểm | Cần category + khung giờ, lấy candidate thật từ provider |
| Xóa điểm | Cần đúng tên stop, tính lại route tới điểm kế tiếp |
| Đổi một stop | Kiểm tra overlap, cập nhật route metadata |
| Yêu cầu mơ hồ ("xóa một điểm") | AI hỏi lại, không tự đoán |

---

## 8. Schema

`Stop` — field bắt buộc theo đề:

```text
name, category, indoor, lat, lon, arrive, leave,
travel_minutes_from_prev, weather_note, reason, source
```

`Stop` — field bổ sung:

```text
wait_minutes_from_prev, meal_type, route_geometry,
route_source, traffic_aware, wikipedia_url,
wikipedia_description, just_changed
```

`Itinerary` bổ sung: ngân sách, `sunset`, AQI, nguồn daily info, notes, số slot bỏ trống.

---

## 9. Dữ liệu thật vs dữ liệu mock

**Runtime — luôn dùng dữ liệu thật:**

| Loại dữ liệu | Nguồn |
|---|---|
| Địa điểm/tọa độ | Goong hoặc Geoapify |
| Thời tiết | Open-Meteo |
| AQI | Open-Meteo Air Quality |
| Giờ hoàng hôn | Open-Meteo Forecast |
| Route | TomTom, OSRM, Goong hoặc Mapbox tùy phương tiện |
| Mô tả địa danh | Wikipedia REST API |

Runtime không tự tạo nhà hàng, tọa độ, giờ mở cửa, thời tiết hay thời gian route — thiếu dữ liệu thì trả về trống hoặc ghi rõ `KHÔNG CÓ DỮ LIỆU`.

**Test — dùng mock HTTP response** (trong `backend/tests/`) để test ổn định và không tốn quota: địa điểm/opening hours/geocode, weather/AQI/sunset, TomTom/OSRM/Goong/Mapbox routing, Wikipedia, Groq agent (scenario 5/6). Mock **chỉ** dùng trong test, không dùng trong runtime demo.

---

## 10. Kiểm thử

```powershell
D:\guidepass-ai-test\backend\.venv\Scripts\python.exe -m pytest -q
D:\guidepass-ai-test\backend\.venv\Scripts\python.exe -m compileall -q backend\app backend\tests frontend\streamlit_app.py
D:\guidepass-ai-test\backend\.venv\Scripts\python.exe -m pip check
```

Bộ test bao phủ: service, routing, weather, Wikipedia, meal validation, session chat, API validation, và các kịch bản chỉnh itinerary. Các scenario phụ thuộc Groq thật cần smoke-test thêm khi có API key/quota.

---

## 11. Bonus features đã làm

- Wikipedia enrichment cho địa danh có bài viết
- AQI và giờ hoàng hôn theo ngày
- Bản đồ Leaflet với marker/popup và route geometry
- Cache provider + quota guard cho TomTom
- Lọc theo giờ mở cửa, chọn điểm trong nhà theo thời tiết
- Kiểm tra tính đa dạng bữa ăn, cảnh báo itinerary lệch
- Patch add/edit/delete cục bộ, giữ session state
- Gắn nhãn nguồn route: traffic thật vs ước lượng tĩnh

---

## 12. Giới hạn đã biết

- Session lưu in-memory — phù hợp demo 1 process; restart server sẽ mất session.
- Chưa có dữ liệu giá đáng tin cậy từ provider nên budget chưa tính thành tổng tiền thật.
- Provider POI thiếu key hoặc bị chặn mạng → itinerary có thể có slot bỏ trống.
- Leaflet dùng tile/CDN ngoài — môi trường không có Internet sẽ không tải được nền bản đồ.
- Nominatim/Overpass còn trong code/test nhưng không phải provider runtime mặc định.
