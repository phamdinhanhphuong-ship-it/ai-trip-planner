import httpx
import pytest

from app.services import geocode_service


@pytest.mark.asyncio
async def test_geocode_success_and_cache(monkeypatch):
    call_count = {"n": 0}

    async def fake_fetch(query):
        call_count["n"] += 1
        return [{"geometry": {"location": {"lat": "10.7769", "lng": "106.7009"}}, "formatted_address": "Ho Chi Minh City"}]

    monkeypatch.setattr(geocode_service, "_fetch_goong_geocode", fake_fetch)
    geocode_service.cache.clear()

    result = await geocode_service.geocode("Ho Chi Minh City")
    assert result.error is None
    assert result.latitude == 10.7769
    assert result.longitude == 106.7009

    await geocode_service.geocode("Ho Chi Minh City")
    assert call_count["n"] == 1  # lan 2 phai lay tu cache


@pytest.mark.asyncio
async def test_geocode_no_result_returns_honest_error(monkeypatch):
    async def fake_fetch(query):
        return []

    monkeypatch.setattr(geocode_service, "_fetch_nominatim", fake_fetch)
    monkeypatch.setattr(geocode_service, "_fetch_geoapify_geocode", lambda query: _empty_async_result())
    monkeypatch.setattr(geocode_service, "_fetch_goong_geocode", lambda query: _empty_async_result())
    geocode_service.cache.clear()

    result = await geocode_service.geocode("dia diem khong ton tai xyz123")
    assert result.error is not None
    assert result.latitude is None


@pytest.mark.asyncio
async def test_geocode_http_error_surfaces_error(monkeypatch):
    async def fake_fetch(query):
        raise httpx.HTTPError("boom")

    monkeypatch.setattr(geocode_service, "_fetch_nominatim", fake_fetch)
    monkeypatch.setattr(geocode_service, "_fetch_geoapify_geocode", lambda query: _empty_async_result())
    monkeypatch.setattr(geocode_service, "_fetch_goong_geocode", lambda query: _empty_async_result())
    geocode_service.cache.clear()

    result = await geocode_service.geocode("bat ky dia chi nao")
    assert result.error is not None


@pytest.mark.asyncio
async def test_geocode_falls_back_to_goong_when_nominatim_fails(monkeypatch):
    async def failing_nominatim(query):
        raise httpx.HTTPError("nominatim blocked")

    async def fake_goong(query):
        return [{"geometry": {"location": {"lat": 10.77, "lng": 106.70}}, "formatted_address": "Cho Ben Thanh"}]

    monkeypatch.setattr(geocode_service, "_fetch_nominatim", failing_nominatim)
    monkeypatch.setattr(geocode_service, "_fetch_geoapify_geocode", lambda query: _empty_async_result())
    monkeypatch.setattr(geocode_service, "_fetch_goong_geocode", fake_goong)
    monkeypatch.setattr(geocode_service.settings, "goong_api_key", "fake-key")
    geocode_service.cache.clear()

    result = await geocode_service.geocode("Cho Ben Thanh")
    assert result.error is None
    assert result.source == "goong"
    assert result.latitude == 10.77


@pytest.mark.asyncio
async def test_geocode_honest_error_mentions_both_sources_when_all_fail(monkeypatch):
    async def failing_nominatim(query):
        raise httpx.HTTPError("nominatim blocked")

    monkeypatch.setattr(geocode_service, "_fetch_nominatim", failing_nominatim)
    monkeypatch.setattr(geocode_service, "_fetch_geoapify_geocode", lambda query: _empty_async_result())
    monkeypatch.setattr(geocode_service.settings, "goong_api_key", "")
    geocode_service.cache.clear()

    result = await geocode_service.geocode("dia diem la")
    assert result.error is not None
    assert "Goong" in result.error


def test_coordinates_match_named_city_rejects_wrong_provider_result():
    assert geocode_service._coordinates_match_named_city("Hà Nội", 10.8, 106.7) is False
    assert geocode_service._coordinates_match_named_city("Hà Nội", 21.03, 105.85) is True


async def _empty_async_result():
    return []
