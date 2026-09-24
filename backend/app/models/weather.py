from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class HourlyWeather(BaseModel):
    time: datetime
    temperature_c: float | None = None
    apparent_temperature_c: float | None = None
    precipitation_mm: float | None = None
    precipitation_probability_pct: float | None = None
    weather_code: int | None = None
    wind_speed_kmh: float | None = None


class WeatherResult(BaseModel):
    latitude: float
    longitude: float
    hourly: list[HourlyWeather]
    source: str = "open-meteo"
    fetched_at: datetime
    # Set when the provider call failed; callers must surface this instead of guessing.
    error: str | None = None


class DailyEnvironment(BaseModel):
    date: datetime
    sunset: datetime | None = None
    air_quality_aqi: int | None = None
    air_quality_label: str | None = None
    source: str = "open-meteo"
    error: str | None = None
