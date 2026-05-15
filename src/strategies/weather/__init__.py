"""Weather market strategy -- NOAA forecast data vs Polymarket weather prices.

When NOAA forecasts are available (via update_forecast), uses them directly.
When running in shadow mode without explicit forecasts, auto-detects temperature
markets from the question text and estimates probability using climatological
baselines for known cities.
"""

import re
import structlog
from decimal import Decimal

from src.core.config import Settings
from src.core.strategy_base import MarketData, Strategy, TradeSignal

logger = structlog.get_logger()

NOAA_BASE_URL = "https://api.weather.gov"

# Climatological baselines: average May highs and standard deviations
CITY_CLIMATES: dict[str, tuple[Decimal, Decimal]] = {
    "nyc": (Decimal("72"), Decimal("8")),
    "new york": (Decimal("72"), Decimal("8")),
    "london": (Decimal("64"), Decimal("6")),
    "tokyo": (Decimal("76"), Decimal("7")),
    "austin": (Decimal("88"), Decimal("8")),
    "houston": (Decimal("88"), Decimal("8")),
    "dallas": (Decimal("85"), Decimal("9")),
    "miami": (Decimal("88"), Decimal("5")),
    "seattle": (Decimal("66"), Decimal("8")),
    "chicago": (Decimal("72"), Decimal("10")),
    "denver": (Decimal("74"), Decimal("10")),
    "paris": (Decimal("68"), Decimal("7")),
    "beijing": (Decimal("80"), Decimal("9")),
    "shanghai": (Decimal("78"), Decimal("6")),
    "hong kong": (Decimal("86"), Decimal("4")),
    "seoul": (Decimal("74"), Decimal("8")),
    "singapore": (Decimal("88"), Decimal("3")),
    "mexico city": (Decimal("79"), Decimal("5")),
    "jakarta": (Decimal("88"), Decimal("3")),
    "manila": (Decimal("90"), Decimal("3")),
    "toronto": (Decimal("68"), Decimal("9")),
    "madrid": (Decimal("76"), Decimal("8")),
    "munich": (Decimal("66"), Decimal("8")),
    "warsaw": (Decimal("68"), Decimal("9")),
    "helsinki": (Decimal("58"), Decimal("9")),
    "wellington": (Decimal("56"), Decimal("5")),
}


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
    - "Will the highest temperature in NYC on June 1 exceed 85F?"
    - "Will it rain in London on May 15?"

    Strategy:
    1. Fetch NOAA forecast for the relevant location/date
    2. Estimate probability of the event from the forecast
    3. Compare to the market price
    4. Trade when the edge exceeds minimum threshold

    In shadow mode without explicit forecasts, auto-detects temperature
    markets from the question text and uses climatology baselines.
    """

    def __init__(self, settings: Settings) -> None:
        super().__init__(name="weather", settings=settings)
        self._min_edge = Decimal("0.05")
        self._forecasts: dict[str, WeatherForecast] = {}
        self._forecast_buffer_days = 3
        self._auto_detected: set[str] = set()  # Markets where we auto-detected
        self._last_signal_time: dict[str, float] = {}
        self._signal_cooldown: float = 300.0  # 5 min between signals per weather market

    async def start(self) -> None:
        await super().start()
        logger.info("weather_started", noaa_url=NOAA_BASE_URL)

    async def on_data(self, data: MarketData) -> list[TradeSignal]:
        if self.state.value != "running":
            return []

        # Cooldown check
        import time
        now = time.monotonic()
        last = self._last_signal_time.get(data.condition_id, 0)
        if now - last < self._signal_cooldown:
            return []

        # Ensure we have a forecast for this market
        # Check both condition_id and raw asset_id (data_feed may use either as key)
        if data.condition_id not in self._forecasts:
            # Also check raw entry in case condition_id is the asset_id
            raw_id = data.raw.get("asset_id", "")
            if raw_id and raw_id in self._forecasts:
                # Migrate the forecast to the condition_id key
                self._forecasts[data.condition_id] = self._forecasts[raw_id]
            else:
                self._auto_detect_forecast(data)

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
        size = abs(edge) * Decimal("100")  # $100 per 1% of edge

        logger.info(
            "weather_signal",
            condition_id=data.condition_id,
            estimated_prob=str(estimated_prob),
            market_price=str(data.yes_price),
            edge=str(edge),
            source=forecast.source,
        )

        self._last_signal_time[data.condition_id] = now
        return [
            TradeSignal(
                condition_id=data.condition_id,
                side=side,
                price=price,
                size=size,
                reason=f"Weather edge={edge:.3f}: {forecast.source} est={estimated_prob:.2f} vs mkt={data.yes_price:.2f}",
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

    def _auto_detect_forecast(self, data: MarketData) -> None:
        """Try to create a forecast from the market question automatically.

        Detects temperature and rain questions and creates climatological estimates.
        """
        q = data.question.lower()

        if not q:
            return

        if data.condition_id in self._auto_detected:
            return

        # Check if this looks like a weather market (temperature or rain)
        is_temp = any(kw in q for kw in ("temperature", "temp", "fahrenheit", "°f"))
        is_rain = any(kw in q for kw in ("rain", "precipitation", "snow", "weather"))

        if not is_temp and not is_rain:
            return

        self._auto_detected.add(data.condition_id)

        # Find the city
        city_key = None
        for city in CITY_CLIMATES:
            if city in q:
                city_key = city
                break

        if city_key is None:
            return

        avg_high, std_dev = CITY_CLIMATES[city_key]

        precip_prob = None
        if is_rain:
            # Rough climatology-based rain probability
            precip_prob = Decimal("0.30") if "london" in city_key else Decimal("0.20")

        self._forecasts[data.condition_id] = WeatherForecast(
            location=city_key,
            high_temp_f=avg_high,
            precipitation_prob=precip_prob,
            source=f"climatology_{city_key}",
        )
        logger.info(
            "weather_auto_detected",
            condition_id=data.condition_id[:16],
            question=data.question[:60],
            city=city_key,
            avg_high=str(avg_high),
        )

    def _estimate_probability(self, question: str, forecast: WeatherForecast) -> Decimal | None:
        """Convert a weather forecast into a probability estimate for a market question."""
        question_lower = question.lower()

        # Temperature range markets: "Will the highest temperature be between 76-77F?"
        range_match = re.search(r"between\s+(\d+)\s*-\s*(\d+)\s*", question_lower)
        if range_match:
            low_f = Decimal(range_match.group(1))
            high_f = Decimal(range_match.group(2))
            if forecast.high_temp_f is None:
                return None
            # Estimate probability that temp falls in [low, high]
            # Using a normal distribution centered on the forecast
            avg = forecast.high_temp_f
            std_dev = Decimal("8")  # Default std dev
            mid_range = (low_f + high_f) / Decimal("2")
            diff = avg - mid_range
            z = diff / std_dev
            # Approximate: probability of being in a 1-2 degree range
            # is roughly the PDF value * range width
            range_width = high_f - low_f
            if abs(z) > Decimal("3"):
                return Decimal("0.01")
            # Linear approximation around the mean
            pdf_approx = Decimal("0.4") - abs(z) * Decimal("0.1")  # Simplified
            pdf_approx = max(Decimal("0.01"), pdf_approx)
            prob = pdf_approx * range_width
            return max(Decimal("0.01"), min(Decimal("0.99"), prob))

        # Temperature exceedance markets: "Will high temp exceed X F?"
        if "exceed" in question_lower or "above" in question_lower or "higher" in question_lower:
            if forecast.high_temp_f is None:
                return None
            threshold = self._extract_temp_threshold(question_lower)
            if threshold is None:
                return None

            diff = forecast.high_temp_f - threshold
            std_dev = Decimal("3")  # NOAA forecast typical std dev for day-ahead
            if std_dev == 0:
                return None
            z_score = diff / std_dev

            if z_score > Decimal("3"):
                return Decimal("0.99")
            elif z_score < Decimal("-3"):
                return Decimal("0.01")
            else:
                return Decimal("0.5") + z_score * Decimal("0.2")

        # Precipitation markets: "Will it rain in X?"
        if "rain" in question_lower or "precipitation" in question_lower:
            if forecast.precipitation_prob is not None:
                return forecast.precipitation_prob

        return None

    def _extract_temp_threshold(self, question: str) -> Decimal | None:
        """Extract temperature threshold from a market question string."""
        match = re.search(r"(\d+)\s*°?\s*[fF]", question)
        if match:
            return Decimal(match.group(1))
        return None
