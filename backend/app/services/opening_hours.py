from __future__ import annotations

import re
from datetime import datetime, time

_WEEKDAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
_DAY_TOKEN_RE = re.compile(r"^[A-Za-z]{2}(-[A-Za-z]{2})?(,[A-Za-z]{2}(-[A-Za-z]{2})?)*$")


def _parse_time_range(range_str: str) -> tuple[time, time] | None:
    match = re.match(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$", range_str.strip())
    if not match:
        return None
    h1, m1, h2, m2 = map(int, match.groups())
    try:
        return time(h1, m1), time(h2 % 24, m2)
    except ValueError:
        return None


def _weekday_matches(day_part: str, weekday_code: str) -> bool:
    idx = _WEEKDAYS.index(weekday_code)
    for token in day_part.split(","):
        if "-" in token:
            start, end = token.split("-")
            if start not in _WEEKDAYS or end not in _WEEKDAYS:
                return False
            start_i, end_i = _WEEKDAYS.index(start), _WEEKDAYS.index(end)
            if start_i <= end_i:
                if start_i <= idx <= end_i:
                    return True
            elif idx >= start_i or idx <= end_i:  # khoảng vắt qua tuần, vd Fr-Mo
                return True
        elif token == weekday_code:
            return True
    return False


def is_open_at(opening_hours_raw: str | None, when: datetime) -> bool | None:
    """Trả True/False nếu parse được cú pháp opening_hours phổ biến, None nếu không chắc chắn.

    Không bao giờ đoán khi gặp cú pháp lạ — trả None để tầng gọi xử lý minh bạch.
    """
    if not opening_hours_raw:
        return None
    raw = opening_hours_raw.strip()
    if raw in ("24/7",):
        return True

    weekday_code = _WEEKDAYS[when.weekday()]
    rules = [r.strip() for r in raw.split(";") if r.strip()]
    if not rules:
        return None

    matched_day_rule = False
    for rule in rules:
        parts = rule.split(" ", 1)
        if len(parts) == 2 and _DAY_TOKEN_RE.match(parts[0]):
            day_part, time_part = parts
            if not _weekday_matches(day_part, weekday_code):
                continue
            matched_day_rule = True
        else:
            day_part, time_part = None, rule
            matched_day_rule = True

        if time_part.strip().lower() in ("off", "closed"):
            return False

        for time_range in time_part.split(","):
            parsed = _parse_time_range(time_range)
            if not parsed:
                return None
            start, end = parsed
            if start <= when.time() <= end:
                return True

    if matched_day_rule:
        return False
    return None
