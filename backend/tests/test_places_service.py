from datetime import datetime

import httpx
import pytest

from app.services import places_service

ORIGIN = (10.7769, 106.7009)

SAMPLE_OVERPASS_RESPONSE = {
    "elements": [
        {
            "type": "node",
            "lat": 10.7770,
            "lon": 106.7010,
            "tags": {"amenity": "restaurant", "name": "Quan A", "opening_hours": "08:00-22:00"},
        },
        {
            "type": "node",
            "lat": 10.7800,
            "lon": 106.7100,
            "tags": {"amenity": "restaurant", "name": "Quan B"},  # thieu opening_hours
        },
        {
            "type": "way",
            "center": {"lat": 10.7790, "lon": 106.7050},
            "tags": {"tourism": "museum", "name": "Bao tang C"},
        },
    ]
}


@pytest.mark.asyncio
async def test_search_places_parses_and_caches(monkeypatch):
    call_count = {"n": 0}

    async def fake_overpass(query):
        call_count["n"] += 1
        return SAMPLE_OVERPASS_RESPONSE

    async def fake_goong(lat, lon, radius_m, categories):
        call_count["n"] += 1
        return [
            {"category": "restaurant", "distance_m": 100, "result": {"name": "Quan A", "formatted_address": "Dia chi A", "geometry": {"location": {"lat": 10.777, "lng": 106.701}}}},
            {"category": "restaurant", "distance_m": 200, "result": {"name": "Quan B", "formatted_address": "Dia chi B", "geometry": {"location": {"lat": 10.778, "lng": 106.702}}}},
            {"category": "museum", "distance_m": 300, "result": {"name": "Bao tang C", "formatted_address": "Dia chi C", "geometry": {"location": {"lat": 10.779, "lng": 106.705}}}},
        ]

    monkeypatch.setattr(places_service, "_fetch_goong_places", fake_goong)
    monkeypatch.setattr(places_service, "_enrich_attractions_with_wikipedia", lambda places: _empty_async_result())
    places_service.cache.clear()

    result = await places_service.search_places(*ORIGIN, radius_m=2000, categories=["restaurant", "museum"], min_restaurant_results=1)

    assert result.error is None
    assert len(result.places) == 3
    quan_b = next(p for p in result.places if p.name == "Quan B")
    assert quan_b.opening_hours_known is False  # khong duoc bia gio mo cua

    await places_service.search_places(*ORIGIN, radius_m=2000, categories=["restaurant", "museum"], min_restaurant_results=1)
    assert call_count["n"] == 1  # lan 2 lay tu cache


@pytest.mark.asyncio
async def test_wikipedia_enrichment_only_targets_landmarks(monkeypatch):
    places = [
        places_service.Place(name="Quan An", category="restaurant", latitude=10.77, longitude=106.70),
        places_service.Place(name="Bao Tang", category="museum", latitude=10.78, longitude=106.70),
    ]
    queried = []

    async def fake_summary(name):
        queried.append(name)
        return {"description": "Mo ta", "wikipedia_url": "https://vi.wikipedia.org/wiki/Test"}

    monkeypatch.setattr(places_service, "get_place_summary", fake_summary)
    await places_service._enrich_attractions_with_wikipedia(places)

    assert queried == ["Bao Tang"]
    assert places[0].wikipedia_url is None
    assert places[1].wikipedia_url == "https://vi.wikipedia.org/wiki/Test"


@pytest.mark.asyncio
async def test_search_places_falls_back_to_geoapify_when_overpass_fails(monkeypatch):
    async def failing_overpass(lat, lon, radius_m, categories):
        raise httpx.HTTPError("overpass down")

    async def fake_geoapify(lat, lon, radius_m, categories):
        return [
            {
                "properties": {"name": "Quan Fallback", "categories": ["catering.restaurant"]},
                "geometry": {"coordinates": [106.701, 10.777]},
            }
        ]

    monkeypatch.setattr(places_service, "_fetch_goong_places", failing_overpass)
    monkeypatch.setattr(places_service, "_fetch_geoapify", fake_geoapify)
    monkeypatch.setattr(places_service.settings, "geoapify_api_key", "fake-key")
    places_service.cache.clear()

    result = await places_service.search_places(*ORIGIN, radius_m=1000, categories=["restaurant"])

    assert "geoapify" in result.sources_used
    assert any(p.name == "Quan Fallback" for p in result.places)
    assert result.error is None


async def _empty_async_result(*args, **kwargs):
    return None


def test_filter_by_distance_and_category():
    from app.models.places import Place

    places = [
        Place(name="Gan", category="restaurant", latitude=10.777, longitude=106.701, distance_m=100),
        Place(name="Xa", category="restaurant", latitude=10.9, longitude=106.9, distance_m=5000),
        Place(name="Bao tang", category="museum", latitude=10.78, longitude=106.71, distance_m=200),
    ]
    near = places_service.filter_by_distance(places, max_distance_m=1000)
    assert {p.name for p in near} == {"Gan", "Bao tang"}

    restaurants = places_service.filter_by_category(places, ["restaurant"])
    assert {p.name for p in restaurants} == {"Gan", "Xa"}


def test_filter_open_at_separates_unknown_from_open():
    from app.models.places import Place

    when = datetime(2026, 9, 21, 12, 0)
    places = [
        Place(name="Mo", category="restaurant", latitude=0, longitude=0, opening_hours_raw="08:00-22:00", opening_hours_known=True),
        Place(name="Dong", category="restaurant", latitude=0, longitude=0, opening_hours_raw="23:00-23:59", opening_hours_known=True),
        Place(name="Khong ro", category="restaurant", latitude=0, longitude=0, opening_hours_raw=None, opening_hours_known=False),
    ]
    open_places, unknown_places = places_service.filter_open_at(places, when)
    assert {p.name for p in open_places} == {"Mo"}
    assert {p.name for p in unknown_places} == {"Khong ro"}
