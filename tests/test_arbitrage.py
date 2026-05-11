"""Tests for the arbitrage strategy."""

import pytest
from decimal import Decimal

from src.core.config import Settings
from src.core.data_feed import DataFeed
from src.core.strategy_base import MarketData
from src.strategies.arbitrage import ArbitrageStrategy


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
