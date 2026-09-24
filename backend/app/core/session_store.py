from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
import asyncio

from app.models.itinerary import DayConstraints, Itinerary


@dataclass
class SessionState:
    constraints: DayConstraints | None = None
    itinerary: Itinerary | None = None
    target_date: date | None = None
    pending_message: str = ""
    pending_weather_query: str = ""


_sessions: dict[str, SessionState] = {}
_lock = threading.Lock()
_session_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def get_session(session_id: str) -> SessionState:
    with _lock:
        if session_id not in _sessions:
            _sessions[session_id] = SessionState()
        return _sessions[session_id]


def reset_session(session_id: str) -> None:
    with _lock:
        _sessions.pop(session_id, None)


def get_session_lock(session_id: str) -> asyncio.Lock:
    """Serialize turns for one session while allowing different sessions to run in parallel."""
    return _session_locks[session_id]
