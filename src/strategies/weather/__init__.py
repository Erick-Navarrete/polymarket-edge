"""Weather market strategy — NOAA forecast data vs Polymarket weather prices."""

import structlog
from decimal import Decimal

from src.core.config import Settings
from src.core.strategy_base import MarketData, Strategy, TradeSignal

logger = structlog.get_logger()

NOAA_BASE_URL = "https://api.weather.gov"


class WeatherForecast:
    """Parsed NOAA forecast for a location."""

    def __init__(
        self,
        location: str,
        high_temp_f: Decimal | None = None,
        low_temp_f: Decimal | None = None,
        precipitation_prob: Decimal | None = None,
        source: str = "NOAA",
    ) -> None:
        self.location = location
        self.high_temp_f = high_temp_f
        self.low_temp_f = low_temp_f
        self.precipitation_prob = precipitation_prob
        self.source = source


class WeatherStrategy(Strategy):
    """Compare NOAA weather forecasts to Polymarket weather market prices.

    Polymarket lists markets like:
    - "Will the high temperature in NYC on June 1 exceed 85°F?"
    - "Will it rain in London on May 15?"

    Strategy:
    1. Fetch NOAA forecast for the relevant location/date
    2. Estimate probability of the event from the forecast
    3. Compare to the market price
    4. Trade when the edge exceeds minimum threshold
    """

    def __init__(self, settings: Settings) -> None:
        super().__init__(name="weather", settings=settings)
        self._min_edge = Decimal("0.05")
        self._forecasts: dict[str, WeatherForecast] = {}
        self._forecast_buffer_days = 3  # Don't trade markets >3 days out ( forecast uncertainty)

    async def start(self) -> None:
        await super().start()
        logger.info("weather_started", noaa_url=NOAA_BASE_URL)

    async def on_data(self, data: MarketData) -> list[TradeSignal]:
        if self.state.value != "running":
            return []

        forecast = self._forecasts.get(data.condition_id)
        if not forecast:
            return []

        # Estimate probability from forecast
        estimated_prob = self._estimate_probability(data.question, forecast)
        if estimated_prob is None:
            return []

        edge = estimated_prob - data.yes_price

        if abs(edge) < self._min_edge:
            return []

        side = "BUY_YES" if edge > 0 else "BUY_NO"
        price = data.yes_price if side == "BUY_YES" else data.no_price
        # Size based on edge magnitude (larger edge = larger position)
        size = abs(edge) * Decimal("100")  # $100 per 1% of edge

        logger.info(
            "weather_signal",
            condition_id=data.condition_id,
            estimated_prob=str(estimated_prob),
            market_price=str(data.yes_price),
            edge=str(edge),
        )

        return [
            TradeSignal(
                condition_id=data.condition_id,
                side=side,
                price=price,
                size=size,
                reason=f"Weather edge={edge:.3f}: NOAA est={estimated_prob:.2f} vs mkt={data.yes_price:.2f}",
                confidence=float(abs(edge)),
                strategy=self.name,
            )
        ]

    async def on_fill(self, signal: TradeSignal, fill_price: Decimal, fill_size: Decimal) -> None:
        self._total_pnl += (signal.price - fill_price) * fill_size
        logger.info(
            "weather_fill",
            condition_id=signal.condition_id,
            side=signal.side,
            fill_price=str(fill_price),
            fill_size=str(fill_size),
        )

    def update_forecast(self, condition_id: str, forecast: WeatherForecast) -> None:
        """Update the NOAA forecast for a market."""
        self._forecasts[condition_id] = forecast

    def _estimate_probability(self, question: str, forecast: WeatherForecast) -> Decimal | None:
        """Convert a weather forecast into a probability estimate for a market question.

        This is a simplified implementation. A production version would:
        - Use ensemble forecast data (not just point estimates)
        - Apply calibration from historical forecast accuracy
        - Handle the full range of weather market question formats
        """
        question_lower = question.lower()

        # Temperature exceedance markets: "Will high temp exceed X°F?"
        if "exceed" in question_lower or "above" in question_lower or "higher" in question_lower:
            if forecast.high_temp_f is None:
                return None
            # Extract temperature threshold from question
            threshold = self._extract_temp_threshold(question_lower)
            if threshold is None:
                return None

            # Simple normal CDF approximation based on forecast ± 3°F std dev
            diff = forecast.high_temp_f - threshold
            std_dev = Decimal("3")  # NOAA forecast typical std dev for day-ahead
            if std_dev == 0:
                return None
            z_score = diff / std_dev

            # Rough Phi function: probability above threshold
            if z_score > Decimal("3"):
                return Decimal("0.99")
            elif z_score < Decimal("-3"):
                return Decimal("0.01")
            else:
                # Approximate normal CDF
                return Decimal("0.5") + z_score * Decimal("0.2")  # Linear approximation near center

        # Precipitation markets: "Will it rain in X?"
        if "rain" in question_lower or "precipitation" in question_lower:
            if forecast.precipitation_prob is not None:
                return forecast.precipitation_prob

        return None

    def _extract_temp_threshold(self, question: str) -> Decimal | None:
        """Extract temperature threshold from a market question string."""
        import re

        # Look for patterns like "85°F", "85 F", "85 degrees"
        match = re.search(r"(\d+)\s*°?\s*[fF]", question)
        if match:
            return Decimal(match.group(1))
        return None
