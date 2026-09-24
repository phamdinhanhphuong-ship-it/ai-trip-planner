from datetime import datetime

import httpx
import pytest

from app.services import routing_service

ORIGIN = (10.7769, 106.7009)
DEST = (10.8231, 106.6297)
DEPART_AT = datetime(2026, 9, 21, 18, 0)

SAMPLE_TOMTOM_RESPONSE = {
    "routes": [{"summary": {"lengthInMeters": 12000, "travelTimeInSeconds": 2400}}]
}
SAMPLE_OSRM_RESPONSE = {"routes": [{"distance": 11800.0, "duration": 1500.0}]}
SAMPLE_GOONG_RESPONSE = {
    "routes": [{"legs": [{"distance": {"value": 10613}, "duration": {"value": 1938}}]}]
}
SAMPLE_MAPBOX_RESPONSE = {"routes": [{"distance": 1800.0, "duration": 1320.0}]}


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    routing_service.cache.clear()
    monkeypatch.setattr(routing_service.settings, "tomtom_api_key", "fake-key")
    monkeypatch.setattr(routing_service.settings, "mapbox_api_key", "fake-mapbox-key")


@pytest.mark.asyncio
async def test_get_route_uses_tomtom_when_available(monkeypatch):
    async def fake_tomtom(origin, destination, depart_at):
        return SAMPLE_TOMTOM_RESPONSE

    monkeypatch.setattr(routing_service, "_fetch_tomtom", fake_tomtom)

    result = await routing_service.get_route(ORIGIN, DEST, DEPART_AT)

    assert result.source == "tomtom"
    assert result.traffic_aware is True
    assert result.distance_m == 12000
    assert result.duration_s == 2400
    assert result.error is None


@pytest.mark.asyncio
async def test_get_route_falls_back_to_osrm_when_tomtom_fails(monkeypatch):
    async def failing_tomtom(origin, destination, depart_at):
        raise httpx.HTTPError("tomtom down")

    async def fake_osrm(origin, destination):
        return SAMPLE_OSRM_RESPONSE

    monkeypatch.setattr(routing_service, "_fetch_tomtom", failing_tomtom)
    monkeypatch.setattr(routing_service, "_fetch_osrm", fake_osrm)

    result = await routing_service.get_route(ORIGIN, DEST, DEPART_AT)

    assert result.source == "osrm"
    assert result.traffic_aware is False
    assert result.distance_m == 11800.0
    assert result.error is None


@pytest.mark.asyncio
async def test_get_route_skips_tomtom_when_no_api_key(monkeypatch):
    monkeypatch.setattr(routing_service.settings, "tomtom_api_key", "")
    call_count = {"n": 0}

    async def should_not_be_called(*args, **kwargs):
        call_count["n"] += 1
        return SAMPLE_TOMTOM_RESPONSE

    async def fake_osrm(origin, destination):
        return SAMPLE_OSRM_RESPONSE

    monkeypatch.setattr(routing_service, "_fetch_tomtom", should_not_be_called)
    monkeypatch.setattr(routing_service, "_fetch_osrm", fake_osrm)

    result = await routing_service.get_route(ORIGIN, DEST, DEPART_AT)

    assert result.source == "osrm"
    assert call_count["n"] == 0


@pytest.mark.asyncio
async def test_get_route_skips_tomtom_when_quota_exceeded(monkeypatch):
    monkeypatch.setattr(routing_service, "tomtom_quota_available", lambda: False)
    call_count = {"n": 0}

    async def should_not_be_called(*args, **kwargs):
        call_count["n"] += 1
        return SAMPLE_TOMTOM_RESPONSE

    async def fake_osrm(origin, destination):
        return SAMPLE_OSRM_RESPONSE

    monkeypatch.setattr(routing_service, "_fetch_tomtom", should_not_be_called)
    monkeypatch.setattr(routing_service, "_fetch_osrm", fake_osrm)

    result = await routing_service.get_route(ORIGIN, DEST, DEPART_AT)

    assert result.source == "osrm"
    assert call_count["n"] == 0


@pytest.mark.asyncio
async def test_get_route_reports_error_when_both_fail(monkeypatch):
    async def failing_tomtom(origin, destination, depart_at):
        raise httpx.HTTPError("tomtom down")

    async def failing_osrm(origin, destination):
        raise httpx.HTTPError("osrm down")

    monkeypatch.setattr(routing_service, "_fetch_tomtom", failing_tomtom)
    monkeypatch.setattr(routing_service, "_fetch_osrm", failing_osrm)

    result = await routing_service.get_route(ORIGIN, DEST, DEPART_AT)

    assert result.source == "none"
    assert result.error is not None


@pytest.mark.asyncio
async def test_get_route_caches_result(monkeypatch):
    call_count = {"n": 0}

    async def fake_tomtom(origin, destination, depart_at):
        call_count["n"] += 1
        return SAMPLE_TOMTOM_RESPONSE

    monkeypatch.setattr(routing_service, "_fetch_tomtom", fake_tomtom)

    await routing_service.get_route(ORIGIN, DEST, DEPART_AT)
    await routing_service.get_route(ORIGIN, DEST, DEPART_AT)

    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_get_route_uses_goong_for_motorcycle(monkeypatch):
    async def fake_goong(origin, destination, travel_mode):
        assert travel_mode == "motorcycle"
        return SAMPLE_GOONG_RESPONSE

    monkeypatch.setattr(routing_service, "_fetch_goong", fake_goong)

    result = await routing_service.get_route(ORIGIN, DEST, DEPART_AT, "motorcycle")

    assert result.source == "goong"
    assert result.travel_mode == "motorcycle"
    assert result.distance_m == 10613
    assert result.duration_s == 1938
    assert result.traffic_aware is False


@pytest.mark.asyncio
async def test_get_route_uses_mapbox_for_walking(monkeypatch):
    async def fake_mapbox(origin, destination):
        return SAMPLE_MAPBOX_RESPONSE

    monkeypatch.setattr(routing_service, "_fetch_mapbox_walking", fake_mapbox)

    result = await routing_service.get_route(ORIGIN, DEST, DEPART_AT, "walking")

    assert result.source == "mapbox"
    assert result.travel_mode == "walking"
    assert result.distance_m == 1800
    assert result.duration_s == 1320
    assert result.traffic_aware is False


@pytest.mark.asyncio
async def test_get_route_reports_missing_walking_provider(monkeypatch):
    monkeypatch.setattr(routing_service.settings, "mapbox_api_key", "")
    monkeypatch.setattr(routing_service, "_fetch_tomtom", lambda *args: pytest.fail("must not call TomTom"))
    monkeypatch.setattr(routing_service, "_fetch_osrm", lambda *args: pytest.fail("must not call OSRM"))

    result = await routing_service.get_route(ORIGIN, DEST, DEPART_AT, "walking")

    assert result.source == "none"
    assert result.travel_mode == "walking"
    assert result.error is not None


@pytest.mark.asyncio
async def test_compare_departure_times_warns_on_mixed_sources(monkeypatch):
    call_state = {"n": 0}

    async def flaky_tomtom(origin, destination, depart_at):
        call_state["n"] += 1
        if call_state["n"] == 1:
            return SAMPLE_TOMTOM_RESPONSE
        raise httpx.HTTPError("tomtom quota hit mid-comparison")

    async def fake_osrm(origin, destination):
        return SAMPLE_OSRM_RESPONSE

    monkeypatch.setattr(routing_service, "_fetch_tomtom", flaky_tomtom)
    monkeypatch.setattr(routing_service, "_fetch_osrm", fake_osrm)

    results = await routing_service.compare_departure_times(
        ORIGIN, DEST, [datetime(2026, 9, 21, 18, 0), datetime(2026, 9, 21, 20, 0)]
    )

    assert {r.source for r in results} == {"tomtom", "osrm"}
    assert all("Cảnh báo" in (r.error or "") for r in results)


def test_describe_source_labels_are_explicit():
    from app.models.routing import RouteResult

    tomtom_result = RouteResult(
        origin=ORIGIN, destination=DEST, depart_at=DEPART_AT, source="tomtom",
        fetched_at=DEPART_AT, traffic_aware=True,
    )
    osrm_result = RouteResult(
        origin=ORIGIN, destination=DEST, depart_at=DEPART_AT, source="osrm",
        fetched_at=DEPART_AT, traffic_aware=False,
    )
    assert "TomTom" in routing_service.describe_source(tomtom_result)
    assert "KHÔNG phải traffic thực" in routing_service.describe_source(osrm_result)
