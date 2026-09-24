from app.core import quota


def test_increment_and_get_monthly_count(monkeypatch):
    quota.cache.clear()
    assert quota.get_monthly_count("test_prefix") == 0

    quota.increment_monthly_count("test_prefix")
    quota.increment_monthly_count("test_prefix")
    assert quota.get_monthly_count("test_prefix") == 2
