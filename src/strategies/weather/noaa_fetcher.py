"""NOAA weather forecast fetcher for weather market strategy."""

import re
from decimal import Decimal

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from src.core.config import Settings
from src.strategies.weather import WeatherForecast

logger = structlog.get_logger()

NOAA_POINTS_URL = "https://api.weather.gov/points/{lat},{lon}"


class NOAAFetcher:
    """Fetch weather forecasts from NOAA API for comparison with Polymarket prices."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._http = httpx.AsyncClient(
            base_url="https://api.weather.gov",
            timeout=30,
            headers={"User-Agent": "PolymarketEdge/0.1.0 (research)"},
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def get_forecast_url(self, lat: float, lon: float) -> str | None:
        """Get the forecast endpoint URL for a location."""
        try:
            resp = await self._http.get(f"/points/{lat},{lon}")
            resp.raise_for_status()
            data = resp.json()
            return data.get("properties", {}).get("forecast", "")
        except Exception as e:
            logger.warning("noaa_points_failed", lat=lat, lon=lon, error=str(e))
            return None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def get_forecast(self, lat: float, lon: float) -> WeatherForecast | None:
        """Fetch the full forecast for a location."""
        forecast_url = await self.get_forecast_url(lat, lon)
        if not forecast_url:
            return None

        try:
            # Use the full URL from NOAA (different host)
            async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "PolymarketEdge/0.1.0"}) as client:
                resp = await client.get(forecast_url)
                resp.raise_for_status()
                data = resp.json()

            periods = data.get("properties", {}).get("periods", [])
            if not periods:
                return None

            today = periods[0]  # First period is the nearest forecast
            return WeatherForecast(
                location=f"{lat},{lon}",
                high_temp_f=Decimal(str(today.get("temperature", 0))) if today.get("temperatureUnit") == "F" else None,
                low_temp_f=self._extract_low_temp(periods),
                precipitation_prob=self._extract_precip_prob(today),
                source="NOAA",
            )

        except Exception as e:
            logger.warning("noaa_forecast_failed", error=str(e))
            return None

    def _extract_low_temp(self, periods: list[dict]) -> Decimal | None:
        """Extract tonight's low temperature from forecast periods."""
        for p in periods:
            if p.get("name", "").lower() in ("tonight", "overnight"):
                if p.get("temperatureUnit") == "F":
                    return Decimal(str(p.get("temperature", 0)))
        return None

    def _extract_precip_prob(self, period: dict) -> Decimal | None:
        """Extract probability of precipitation as a decimal (0-1)."""
        prob_str = period.get("probabilityOfPrecipitation", {}).get("value")
        if prob_str is not None:
            return Decimal(str(prob_str)) / Decimal("100")
        return None

    @staticmethod
    def parse_location_from_question(question: str) -> tuple[float, float] | None:
        """Attempt to parse a city name from a Polymarket question and return lat/lon.

        This is a simple lookup for common cities. A production version
        would use a geocoding API.
        """
        city_coords = {
            "new york": (40.7128, -74.0060),
            "nyc": (40.7128, -74.0060),
            "los angeles": (34.0522, -118.2437),
            "chicago": (41.8781, -87.6298),
            "london": (51.5074, -0.1278),
            "miami": (25.7617, -80.1918),
            "dallas": (32.7767, -96.7970),
            "denver": (39.7392, -104.9903),
            "seattle": (47.6062, -122.3321),
            "boston": (42.3601, -71.0589),
            "atlanta": (33.7490, -84.3880),
            "houston": (29.7604, -95.3698),
            "phoenix": (33.4484, -112.0740),
            "san francisco": (37.7749, -122.4194),
            "washington dc": (38.9072, -77.0369),
            "dc": (38.9072, -77.0369),
        }

        question_lower = question.lower()
        for city, coords in city_coords.items():
            if city in question_lower:
                return coords
        return None

    async def close(self) -> None:
        await self._http.aclose()
