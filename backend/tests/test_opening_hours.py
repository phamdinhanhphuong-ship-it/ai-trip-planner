from datetime import datetime

from app.services.opening_hours import is_open_at


def test_24_7_always_open():
    assert is_open_at("24/7", datetime(2026, 9, 21, 3, 0)) is True


def test_simple_daily_range_open_and_closed():
    raw = "08:00-22:00"
    assert is_open_at(raw, datetime(2026, 9, 21, 18, 0)) is True
    assert is_open_at(raw, datetime(2026, 9, 21, 23, 0)) is False


def test_weekday_range_matches_correct_day():
    raw = "Mo-Fr 08:00-17:00"
    # 2026-09-21 la thu Hai (Monday)
    assert is_open_at(raw, datetime(2026, 9, 21, 10, 0)) is True
    # 2026-09-26 la thu Bay (Saturday) -> khong khop ngay -> khong chac chan
    assert is_open_at(raw, datetime(2026, 9, 26, 10, 0)) is None


def test_multiple_rules_separated_by_semicolon():
    raw = "Mo-Fr 08:00-12:00,13:00-17:00; Sa 08:00-12:00"
    assert is_open_at(raw, datetime(2026, 9, 21, 14, 0)) is True  # thu Hai buoi chieu
    assert is_open_at(raw, datetime(2026, 9, 21, 12, 30)) is False  # gio nghi trua
    assert is_open_at(raw, datetime(2026, 9, 26, 9, 0)) is True  # thu Bay


def test_off_keyword_returns_closed():
    assert is_open_at("Su off", datetime(2026, 9, 27, 10, 0)) is False


def test_missing_or_unparseable_returns_none():
    assert is_open_at(None, datetime(2026, 9, 21, 10, 0)) is None
    assert is_open_at("sunrise-sunset", datetime(2026, 9, 21, 10, 0)) is None
