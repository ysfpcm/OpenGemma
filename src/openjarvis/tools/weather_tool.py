"""Weather lookup tool using Open-Meteo API."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

logger = logging.getLogger(__name__)


@ToolRegistry.register("weather_lookup")
class WeatherTool(BaseTool):
    """Lookup current weather and short-term forecast."""

    tool_id = "weather_lookup"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="weather_lookup",
            description="Lookup real-time weather data for a specific location.",
            parameters={
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "City name, or city and state/country (e.g., 'San Francisco, CA' or 'London, UK').",
                    },
                },
                "required": ["location"],
            },
            category="search",
        )

    def execute(self, **params: Any) -> ToolResult:
        location = params.get("location", "")
        if not location:
            return ToolResult(
                tool_name="weather_lookup",
                content="No location provided.",
                success=False,
            )

        try:
            # Step 1: Geocode location (extract city name before comma)
            search_name = location.split(",")[0].strip()
            geocode_url = "https://geocoding-api.open-meteo.com/v1/search"
            geocode_resp = httpx.get(
                geocode_url,
                params={"name": search_name, "count": 1, "language": "en", "format": "json"},
                timeout=15.0,
            )
            geocode_resp.raise_for_status()
            geocode_data = geocode_resp.json()

            results = geocode_data.get("results", [])
            if not results:
                return ToolResult(
                    tool_name="weather_lookup",
                    content=f"Could not find coordinates for location: {location}",
                    success=False,
                )

            lat = results[0]["latitude"]
            lon = results[0]["longitude"]
            display_name = results[0].get("name", location)
            if "admin1" in results[0]:
                display_name += f", {results[0]['admin1']}"
            if "country" in results[0]:
                display_name += f", {results[0]['country']}"

            # Step 2: Fetch weather
            weather_url = "https://api.open-meteo.com/v1/forecast"
            weather_resp = httpx.get(
                weather_url,
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
                    "temperature_unit": "fahrenheit",
                    "wind_speed_unit": "mph",
                    "precipitation_unit": "inch",
                    "timezone": "auto",
                },
                timeout=15.0,
            )
            weather_resp.raise_for_status()
            weather_data = weather_resp.json()

            current = weather_data.get("current", {})
            current_units = weather_data.get("current_units", {})

            # Map WMO weather codes to string descriptions
            # https://open-meteo.com/en/docs
            wmo_codes = {
                0: "Clear sky",
                1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
                45: "Fog", 48: "Depositing rime fog",
                51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
                61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
                71: "Slight snow fall", 73: "Moderate snow fall", 75: "Heavy snow fall",
                77: "Snow grains",
                80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
                85: "Slight snow showers", 86: "Heavy snow showers",
                95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail"
            }
            code = current.get("weather_code")
            condition = wmo_codes.get(code, "Unknown conditions")

            content = (
                f"Weather for {display_name}:\n"
                f"Condition: {condition}\n"
                f"Temperature: {current.get('temperature_2m')} {current_units.get('temperature_2m', 'F')}\n"
                f"Feels like: {current.get('apparent_temperature')} {current_units.get('apparent_temperature', 'F')}\n"
                f"Humidity: {current.get('relative_humidity_2m')}{current_units.get('relative_humidity_2m', '%')}\n"
                f"Precipitation: {current.get('precipitation')} {current_units.get('precipitation', 'in')}\n"
                f"Wind: {current.get('wind_speed_10m')} {current_units.get('wind_speed_10m', 'mph')}"
            )

            return ToolResult(
                tool_name="weather_lookup",
                content=content,
                success=True,
                metadata={
                    "location": display_name,
                    "latitude": lat,
                    "longitude": lon,
                    "temperature": current.get("temperature_2m"),
                    "condition": condition,
                }
            )

        except Exception as e:
            logger.error("Error looking up weather: %s", e)
            return ToolResult(
                tool_name="weather_lookup",
                content=f"Error looking up weather: {e}",
                success=False,
            )

__all__ = ["WeatherTool"]
