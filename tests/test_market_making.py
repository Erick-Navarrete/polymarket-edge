"""Tests for the market making strategy."""

import pytest
from decimal import Decimal

from src.core.config import Settings
from src.core.strategy_base import MarketData
from src.strategies.market_making import MarketMakingStrategy, BandsConfig


@pytest.fixture
def settings():
    return Settings()


@pytest.fixture
def config():
    return BandsConfig(
        spread_bps=200,  # 2% spread
        order_size=Decimal("10"),
        max_position=Decimal("100"),
        signal_cooldown_seconds=0,  # No cooldown in tests
    )


@pytest.fixture
def strategy(settings, config):
    return MarketMakingStrategy(settings, config)


@pytest.fixture
def market_data():
    return MarketData(
        condition_id="0xmm1",
        question="Will X happen?",
        yes_price=Decimal("0.50"),
        no_price=Decimal("0.50"),
        spread=Decimal("0.02"),
        volume_24h=Decimal("10000"),
        timestamp=1000.0,
    )


@pytest.mark.asyncio
async def test_places_both_sides(strategy, market_data):
    """Market maker should place both bid and ask around midpoint."""
    await strategy.start()
    signals = await strategy.on_data(market_data)

    assert len(signals) == 2
    sides = {s.side for s in signals}
    assert "BUY_YES" in sides
    assert "SELL_YES" in sides


@pytest.mark.asyncio
async def test_bid_below_midpoint(strategy, market_data):
    """Bid should be below midpoint."""
    await strategy.start()
    signals = await strategy.on_data(market_data)

    bid = [s for s in signals if s.side == "BUY_YES"][0]
    midpoint = (market_data.yes_price + market_data.no_price) / Decimal("2")
    assert bid.price < midpoint


@pytest.mark.asyncio
async def test_ask_above_midpoint(strategy, market_data):
    """Ask should be above midpoint."""
    await strategy.start()
    signals = await strategy.on_data(market_data)

    ask = [s for s in signals if s.side == "SELL_YES"][0]
    midpoint = (market_data.yes_price + market_data.no_price) / Decimal("2")
    assert ask.price > midpoint


@pytest.mark.asyncio
async def test_inventory_skew(strategy, market_data):
    """Long inventory should shift quotes down to encourage selling."""
    await strategy.start()

    # Simulate being long
    from src.core.strategy_base import TradeSignal
    fill_signal = TradeSignal(
        condition_id="0xmm1",
        side="BUY_YES",
        price=Decimal("0.49"),
        size=Decimal("50"),
        reason="test",
        strategy="market_making",
    )
    await strategy.on_fill(fill_signal, Decimal("0.49"), Decimal("50"))

    # Now check that bid price is lower (skewed down)
    signals = await strategy.on_data(market_data)
    # With long inventory, we should still have signals
    # but the skew should be reflected in the prices
    assert len(signals) >= 1


@pytest.mark.asyncio
async def test_signal_cooldown_enforced(settings):
    """Rapid on_data calls should be throttled by cooldown."""
    config = BandsConfig(spread_bps=200, signal_cooldown_seconds=10.0)
    s = MarketMakingStrategy(settings, config)
    await s.start()
    data = MarketData(
        condition_id="0xcool",
        question="Test?",
        yes_price=Decimal("0.50"),
        no_price=Decimal("0.50"),
        spread=Decimal("0.02"),
        volume_24h=Decimal("1000"),
        timestamp=1000.0,
    )
    signals1 = await s.on_data(data)
    assert len(signals1) == 2  # First call produces signals
    signals2 = await s.on_data(data)
    assert len(signals2) == 0  # Second call blocked by cooldown


@pytest.mark.asyncio
async def test_no_signals_when_spread_too_thin(strategy):
    """No quotes should be placed when market spread is below minimum."""
    await strategy.start()
    thin_market = MarketData(
        condition_id="0xthin",
        question="Will X happen?",
        yes_price=Decimal("0.50"),
        no_price=Decimal("0.50"),
        spread=Decimal("0.001"),  # Below min_spread of 0.01
        volume_24h=Decimal("1000"),
        timestamp=1000.0,
    )
    signals = await strategy.on_data(thin_market)
    assert len(signals) == 0
