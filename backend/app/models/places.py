from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class GeocodeResult(BaseModel):
    query: str
    latitude: float | None = None
    longitude: float | None = None
    display_name: str | None = None
    source: str = "nominatim"
    fetched_at: datetime
    error: str | None = None


class Place(BaseModel):
    name: str
    category: str
    latitude: float
    longitude: float
    distance_m: float | None = None
    address: str | None = None
    opening_hours_raw: str | None = None
    # False when the source simply has no opening_hours tag — must not be guessed.
    opening_hours_known: bool = False
    cuisine: str | None = None
    description: str | None = None
    wikipedia_url: str | None = None
    source: str = "overpass"


class PlacesResult(BaseModel):
    places: list[Place]
    sources_used: list[str]
    fetched_at: datetime
    error: str | None = None
