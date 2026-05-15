"""Tests for the arbitrage strategy."""

import pytest
from decimal import Decimal

from src.core.config import Settings
from src.core.data_feed import DataFeed
from src.core.strategy_base import MarketData
from src.strategies.arbitrage import ArbitrageStrategy
import time


@pytest.fixture
def settings():
    return Settings()


@pytest.fixture
def data_feed(settings):
    return DataFeed(settings)


@pytest.fixture
def strategy(settings, data_feed):
    return ArbitrageStrategy(settings, data_feed)


@pytest.fixture
def arb_market_data():
    """Market data with YES + NO < $1 (arbitrage opportunity)."""
    return MarketData(
        condition_id="0xarb1",
        question="Will X happen?",
        yes_price=Decimal("0.45"),
        no_price=Decimal("0.50"),  # Total = $0.95, profit = $0.05
        spread=Decimal("0.05"),
        volume_24h=Decimal("10000"),
        timestamp=1000.0,
    )


@pytest.fixture
def no_arb_market_data():
    """Market data with YES + NO >= $1 (no arbitrage)."""
    return MarketData(
        condition_id="0xnoarb",
        question="Will Y happen?",
        yes_price=Decimal("0.55"),
        no_price=Decimal("0.45"),  # Total = $1.00, no profit
        spread=Decimal("0.10"),
        volume_24h=Decimal("5000"),
        timestamp=1000.0,
    )


@pytest.mark.asyncio
async def test_detects_internal_arb(strategy, arb_market_data):
    """Strategy should detect when YES + NO < $1."""
    await strategy.start()
    signals = await strategy.on_data(arb_market_data)

    assert len(signals) == 2
    assert signals[0].side == "BUY_YES"
    assert signals[1].side == "BUY_NO"
    assert signals[0].condition_id == "0xarb1"
    assert signals[1].condition_id == "0xarb1"


@pytest.mark.asyncio
async def test_no_signal_when_no_arb(strategy, no_arb_market_data):
    """Strategy should not generate signals when there's no arb."""
    await strategy.start()
    signals = await strategy.on_data(no_arb_market_data)

    assert len(signals) == 0


@pytest.mark.asyncio
async def test_no_signal_when_paused(strategy, arb_market_data):
    """Paused strategy should not generate signals."""
    await strategy.start()
    await strategy.pause()
    signals = await strategy.on_data(arb_market_data)

    assert len(signals) == 0


@pytest.mark.asyncio
async def test_cross_platform_arb(strategy):
    """Cross-platform arb should detect price gaps between PM and Kalshi."""
    # Enable cross-platform by setting a Kalshi price
    strategy.cross_platform_enabled = True
    await strategy.start()

    data = MarketData(
        condition_id="0xcross1",
        question="Will BTC be above $70k?",
        yes_price=Decimal("0.70"),
        no_price=Decimal("0.30"),
        spread=Decimal("0.02"),
        volume_24h=Decimal("50000"),
        timestamp=1000.0,
    )

    # Kalshi has YES at 0.60 — PM YES is overpriced
    strategy.update_kalshi_price("0xcross1", Decimal("0.60"))

    signals = await strategy.on_data(data)
    # Should generate a SELL_YES signal (PM overpriced)
    assert len(signals) > 0
    assert any(s.side == "SELL_YES" for s in signals)


@pytest.mark.asyncio
async def test_mean_reversion_extreme_high(settings, data_feed):
    """Mean-reversion should buy NO when YES price is extremely high (>=0.95)."""
    # Use fresh strategy with no cooldown interference
    s = ArbitrageStrategy(settings, data_feed)
    s._signal_cooldown = 0  # Disable cooldown for test
    await s.start()

    # Feed 3+ data points to satisfy the min observation count
    for _ in range(3):
        data = MarketData(
            condition_id="0xextreme1",
            question="Is X certain?",
            yes_price=Decimal("0.96"),
            no_price=Decimal("0.04"),
            spread=Decimal("0.02"),
            volume_24h=Decimal("1000"),
            timestamp=1000.0,
        )
        signals = await s.on_data(data)

    # Should detect mean-reversion and suggest buying NO
    assert len(signals) > 0
    assert signals[0].side == "BUY_NO"
    await s.stop()


@pytest.mark.asyncio
async def test_mean_reversion_extreme_low(settings, data_feed):
    """Mean-reversion should buy YES when YES price is extremely low (<=0.05)."""
    s = ArbitrageStrategy(settings, data_feed)
    s._signal_cooldown = 0
    await s.start()

    for _ in range(3):
        data = MarketData(
            condition_id="0xextreme2",
            question="Is X impossible?",
            yes_price=Decimal("0.03"),
            no_price=Decimal("0.97"),
            spread=Decimal("0.02"),
            volume_24h=Decimal("1000"),
            timestamp=1000.0,
        )
        signals = await s.on_data(data)

    assert len(signals) > 0
    assert signals[0].side == "BUY_YES"
    await s.stop()


@pytest.mark.asyncio
async def test_arb_cooldown(settings, data_feed):
    """Rapid on_data calls should be throttled by cooldown."""
    s = ArbitrageStrategy(settings, data_feed)
    s._signal_cooldown = 60.0  # Long cooldown
    await s.start()

    data = MarketData(
        condition_id="0xarb_cool",
        question="Test?",
        yes_price=Decimal("0.45"),
        no_price=Decimal("0.50"),
        spread=Decimal("0.05"),
        volume_24h=Decimal("1000"),
        timestamp=1000.0,
    )
    signals1 = await s.on_data(data)
    assert len(signals1) == 2  # Internal arb detected
    signals2 = await s.on_data(data)
    assert len(signals2) == 0  # Blocked by cooldown
    await s.stop()
