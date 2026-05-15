"""Tests for weather strategy improvements and data feed metadata enrichment."""

import pytest
from decimal import Decimal

from src.core.config import Settings
from src.core.data_feed import DataFeed
from src.core.strategy_base import MarketData
from src.strategies.weather import WeatherStrategy, WeatherForecast
from src.strategies.ai_agent import AIAgentStrategy, ProbabilityEstimate


# --- Weather Strategy Tests ---

@pytest.mark.asyncio
async def test_weather_auto_detect_temperature():
    """Weather strategy auto-detects temperature markets from question text."""
    settings = Settings()
    strategy = WeatherStrategy(settings)
    await strategy.start()

    data = MarketData(
        condition_id="0xnyc_temp",
        question="Will the highest temperature in NYC exceed 85F on May 16?",
        yes_price=Decimal("0.70"),
        no_price=Decimal("0.30"),
        spread=Decimal("0.02"),
        volume_24h=Decimal("5000"),
        timestamp=1000.0,
    )
    signals = await strategy.on_data(data)
    # Should auto-detect NYC and create forecast
    assert "0xnyc_temp" in strategy._forecasts
    assert strategy._forecasts["0xnyc_temp"].source == "climatology_nyc"
    await strategy.stop()


@pytest.mark.asyncio
async def test_weather_auto_detect_rain():
    """Weather strategy auto-detects rain markets and estimates from precipitation_prob."""
    settings = Settings()
    strategy = WeatherStrategy(settings)
    await strategy.start()

    data = MarketData(
        condition_id="0xlondon_rain",
        question="Will it rain in London on May 15?",
        yes_price=Decimal("0.50"),
        no_price=Decimal("0.50"),
        spread=Decimal("0.02"),
        volume_24h=Decimal("5000"),
        timestamp=1000.0,
    )
    signals = await strategy.on_data(data)
    # Should auto-detect London rain market
    assert "0xlondon_rain" in strategy._forecasts
    assert strategy._forecasts["0xlondon_rain"].precipitation_prob is not None
    await strategy.stop()


@pytest.mark.asyncio
async def test_weather_no_auto_detect_non_weather():
    """Weather strategy should skip non-weather questions."""
    settings = Settings()
    strategy = WeatherStrategy(settings)
    await strategy.start()

    data = MarketData(
        condition_id="0xpolitics",
        question="Will the Fed raise interest rates in June?",
        yes_price=Decimal("0.60"),
        no_price=Decimal("0.40"),
        spread=Decimal("0.02"),
        volume_24h=Decimal("5000"),
        timestamp=1000.0,
    )
    signals = await strategy.on_data(data)
    assert "0xpolitics" not in strategy._forecasts
    await strategy.stop()


@pytest.mark.asyncio
async def test_weather_forecast_key_migration():
    """Weather strategy should migrate forecasts from asset_id to condition_id."""
    settings = Settings()
    strategy = WeatherStrategy(settings)
    await strategy.start()

    # Pre-populate with asset_id key (simulating WS data using asset_id)
    forecast = WeatherForecast(location="nyc", high_temp_f=Decimal("72"), source="climatology_nyc")
    strategy.update_forecast("asset_id_123", forecast)

    # Data arrives with condition_id but raw.asset_id matches the key
    data = MarketData(
        condition_id="0xcondition_abc",
        question="Will the highest temperature in NYC exceed 85F?",
        yes_price=Decimal("0.70"),
        no_price=Decimal("0.30"),
        spread=Decimal("0.02"),
        volume_24h=Decimal("5000"),
        timestamp=1000.0,
        raw={"asset_id": "asset_id_123"},
    )
    signals = await strategy.on_data(data)
    # Forecast should be migrated to the condition_id key
    assert "0xcondition_abc" in strategy._forecasts
    await strategy.stop()


# --- Data Feed Metadata Tests ---

def test_data_feed_metadata_enrichment():
    """DataFeed should enrich parsed MarketData with pre-fetched metadata."""
    settings = Settings()
    feed = DataFeed(settings)

    # Manually populate metadata cache
    feed._token_metadata["token_abc"] = {
        "question": "Will BTC hit $100k by Dec?",
        "condition_id": "0xcond_abc",
    }

    # Parse an orderbook snapshot with asset_id matching our metadata
    entry = {
        "asset_id": "token_abc",
        "bids": [{"price": "0.55", "size": "100"}],
        "asks": [{"price": "0.56", "size": "100"}],
        "timestamp": 1000,
    }
    data = feed._parse_orderbook_snapshot(entry)

    assert data is not None
    assert data.condition_id == "0xcond_abc"
    assert data.question == "Will BTC hit $100k by Dec?"


def test_data_feed_price_change_metadata_enrichment():
    """DataFeed should enrich price change data with pre-fetched metadata."""
    settings = Settings()
    feed = DataFeed(settings)

    feed._token_metadata["token_xyz"] = {
        "question": "Will Trump win 2028?",
        "condition_id": "0xcond_xyz",
    }

    change = {
        "asset_id": "token_xyz",
        "best_bid": "0.40",
        "best_ask": "0.42",
        "price": "0",
        "size": "50",
    }
    data = feed._parse_price_change(change, market="")

    assert data is not None
    assert data.condition_id == "0xcond_xyz"
    assert data.question == "Will Trump win 2028?"


def test_data_feed_no_metadata_falls_back():
    """DataFeed should use asset_id when no metadata is available."""
    settings = Settings()
    feed = DataFeed(settings)

    entry = {
        "asset_id": "unknown_token",
        "bids": [{"price": "0.50", "size": "100"}],
        "asks": [{"price": "0.51", "size": "100"}],
        "timestamp": 1000,
    }
    data = feed._parse_orderbook_snapshot(entry)

    assert data is not None
    assert data.condition_id == "unknown_token"
    assert data.question == ""


# --- AI Agent Improvements Tests ---

def test_probability_estimate_staleness():
    """ProbabilityEstimate should detect when it's stale."""
    import time

    # Fresh estimate (created now)
    fresh = ProbabilityEstimate(
        condition_id="0xtest",
        estimated_prob=Decimal("0.6"),
        confidence=Decimal("0.7"),
        reasoning="test",
        created_at=time.monotonic(),
    )
    assert not fresh.is_stale

    # Stale estimate (created 2 hours ago)
    stale = ProbabilityEstimate(
        condition_id="0xtest",
        estimated_prob=Decimal("0.6"),
        confidence=Decimal("0.7"),
        reasoning="test",
        created_at=time.monotonic() - 7200,  # 2 hours ago
    )
    assert stale.is_stale


@pytest.mark.asyncio
async def test_ai_agent_heuristic_logs_properly():
    """AI agent heuristic fallback should work and generate signals for mean-reverting markets."""
    settings = Settings()  # No OPENAI_API_KEY -> heuristic mode
    strategy = AIAgentStrategy(settings)
    await strategy.start()

    # Feed enough price history to trigger the heuristic
    base_price = Decimal("0.55")
    for i in range(5):
        data = MarketData(
            condition_id="0xheuristic_test",
            question="Test market?",
            yes_price=base_price + Decimal(str(i * 0.01)),
            no_price=Decimal("1") - (base_price + Decimal(str(i * 0.01))),
            spread=Decimal("0.02"),
            volume_24h=Decimal("50000"),
            timestamp=1000.0 + i,
        )
        signals = await strategy.on_data(data)

    # With sufficient history and a moderate deviation, heuristic may fire
    # Just verify no crash and returns a list
    data_final = MarketData(
        condition_id="0xheuristic_test",
        question="Test market?",
        yes_price=Decimal("0.65"),  # Moved away from ~0.57 avg
        no_price=Decimal("0.35"),
        spread=Decimal("0.02"),
        volume_24h=Decimal("50000"),
        timestamp=1010.0,
    )
    signals = await strategy.on_data(data_final)
    assert isinstance(signals, list)
    await strategy.stop()
