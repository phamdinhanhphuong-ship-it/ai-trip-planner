import httpx
import pytest

from app.services import wikipedia_service


@pytest.mark.asyncio
async def test_get_place_summary_returns_cached_summary(monkeypatch):
    calls = {"count": 0}

    async def fake_summary(query):
        calls["count"] += 1
        return {
            "extract": "Mo ta ngan ve diem tham quan.",
            "content_urls": {"desktop": {"page": "https://vi.wikipedia.org/wiki/Test"}},
        }

    monkeypatch.setattr(wikipedia_service, "_fetch_summary", fake_summary)
    wikipedia_service.cache.clear()

    first = await wikipedia_service.get_place_summary("Diem tham quan")
    second = await wikipedia_service.get_place_summary("Diem tham quan")

    assert first == second
    assert first["description"] == "Mo ta ngan ve diem tham quan."
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_get_place_summary_returns_none_on_provider_error(monkeypatch):
    async def failing_summary(query):
        raise httpx.HTTPError("wiki down")

    monkeypatch.setattr(wikipedia_service, "_fetch_summary", failing_summary)
    wikipedia_service.cache.clear()

    assert await wikipedia_service.get_place_summary("Diem khong ton tai") is None
