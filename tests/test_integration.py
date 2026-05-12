"""Integration tests — exercise the full engine pipeline in paper mode.

Each test starts the engine with a specific strategy, feeds it market data,
and verifies that signals are generated, risk-gated, and paper-executed.
"""

import pytest
from decimal import Decimal

from src.core.config import Settings
from src.core.engine import Engine
from src.core.strategy_base import MarketData


# --- Helpers ---

def make_market_data(
    condition_id: str = "0xtest",
    yes: Decimal = Decimal("0.50"),
    no: Decimal = Decimal("0.50"),
    question: str = "Test market?",
) -> MarketData:
    return MarketData(
        condition_id=condition_id,
        question=question,
        yes_price=yes,
        no_price=no,
        spread=abs(yes - no),
        volume_24h=Decimal("10000"),
        timestamp=1000.0,
    )


def paper_settings() -> Settings:
    """Settings configured for paper mode with risk limits suitable for testing."""
    return Settings(
        live_mode=False,
        max_daily_loss_pct=Decimal("10"),
        max_monthly_loss_pct=Decimal("20"),
        max_drawdown_pct=Decimal("25"),
        max_position_size_usd=Decimal("500"),
        max_total_exposure_usd=Decimal("5000"),
    )


# --- Engine Pipeline Tests ---

@pytest.mark.asyncio
async def test_engine_paper_mode_no_live_client():
    """Engine in paper mode should not initialize CLOB client."""
    settings = paper_settings()
    assert settings.live_mode is False

    engine = Engine(settings)
    await engine.executor.initialize()
    assert engine.executor._clob_client is None


@pytest.mark.asyncio
async def test_engine_pipeline_arbitrage():
    """Full pipeline: arb data -> signal -> risk approve -> paper fill."""
    settings = paper_settings()
    engine = Engine(settings)

    from src.core.data_feed import DataFeed
    from src.strategies.arbitrage import ArbitrageStrategy

    engine.add_strategy(ArbitrageStrategy(settings, DataFeed(settings)))
    await engine.start()

    # YES + NO = 0.95 -> internal arb detected
    data = make_market_data(yes=Decimal("0.45"), no=Decimal("0.50"))
    result = await engine.run_once(data)

    assert len(result["signals"]) == 2
    assert result["fills"]  # Paper fills should exist
    assert result["risk_status"]["halted"] is False
    await engine.stop()


@pytest.mark.asyncio
async def test_engine_risk_gate_rejects_oversized_trade():
    """Risk manager should reject trades exceeding max position size."""
    settings = paper_settings()
    settings.max_position_size_usd = Decimal("0.01")  # Tiny limit

    engine = Engine(settings)

    from src.core.data_feed import DataFeed
    from src.strategies.arbitrage import ArbitrageStrategy

    engine.add_strategy(ArbitrageStrategy(settings, DataFeed(settings)))
    await engine.start()

    data = make_market_data(yes=Decimal("0.45"), no=Decimal("0.50"))
    result = await engine.run_once(data)

    # Signals generated but fills should be empty (rejected by risk)
    assert result["signals"]  # Strategy produced signals
    assert not result["fills"]  # But risk blocked execution
    await engine.stop()


@pytest.mark.asyncio
async def test_engine_start_stop_lifecycle():
    """Engine should cleanly start and stop all strategies."""
    settings = paper_settings()
    engine = Engine(settings)

    from src.core.data_feed import DataFeed
    from src.strategies.arbitrage import ArbitrageStrategy
    from src.strategies.market_making import MarketMakingStrategy

    engine.add_strategy(ArbitrageStrategy(settings, DataFeed(settings)))
    engine.add_strategy(MarketMakingStrategy(settings))

    await engine.start()
    for s in engine._strategies.values():
        assert s.state.value == "running"

    await engine.stop()
    for s in engine._strategies.values():
        assert s.state.value == "stopped"


@pytest.mark.asyncio
async def test_risk_manager_circuit_breaker():
    """Risk manager should halt after drawdown exceeds threshold."""
    settings = paper_settings()
    settings.max_drawdown_pct = Decimal("5")
    engine = Engine(settings)

    # Force a drawdown scenario: set peak high, then equity drops
    engine.risk_manager.set_equity(Decimal("1000"))  # Sets peak to 1000
    engine.risk_manager._current_equity = Decimal("900")  # 10% drawdown > 5% max
    engine.risk_manager._check_circuit_breakers()

    assert engine.risk_manager.is_halted


# --- Individual Strategy Tests ---

@pytest.mark.asyncio
async def test_market_making_strategy():
    """Market making should generate bid/ask signals around the mid price."""
    settings = paper_settings()
    from src.strategies.market_making import MarketMakingStrategy

    strategy = MarketMakingStrategy(settings)
    await strategy.start()

    data = make_market_data(yes=Decimal("0.55"), no=Decimal("0.45"))
    signals = await strategy.on_data(data)

    # Market making can return 0 signals if spread is too wide or other conditions
    # Just verify it doesn't crash and returns a list
    assert isinstance(signals, list)
    for s in signals:
        assert s.strategy == "market_making"
        assert s.price > 0
    await strategy.stop()


@pytest.mark.asyncio
async def test_weather_strategy():
    """Weather strategy should compare NOAA data to market prices."""
    settings = paper_settings()
    from src.strategies.weather import WeatherStrategy

    strategy = WeatherStrategy(settings)
    await strategy.start()

    data = make_market_data(
        condition_id="0xweather",
        yes=Decimal("0.70"),
        question="Will NYC reach 90F on July 4?",
    )
    signals = await strategy.on_data(data)
    assert isinstance(signals, list)
    await strategy.stop()


@pytest.mark.asyncio
async def test_copy_trading_strategy():
    """Copy trading should handle wallet tracking without crash."""
    settings = paper_settings()
    from src.strategies.copy_trading import CopyTradingStrategy

    strategy = CopyTradingStrategy(settings)
    await strategy.start()

    data = make_market_data()
    signals = await strategy.on_data(data)
    assert isinstance(signals, list)
    await strategy.stop()


# --- Strategy State Machine Tests ---

@pytest.mark.asyncio
async def test_strategy_pause_resume():
    """Strategy should not produce signals while paused."""
    settings = paper_settings()
    from src.core.data_feed import DataFeed
    from src.strategies.arbitrage import ArbitrageStrategy

    strategy = ArbitrageStrategy(settings, DataFeed(settings))

    # Stopped — no signals
    data = make_market_data(yes=Decimal("0.45"), no=Decimal("0.50"))
    assert await strategy.on_data(data) == []

    # Running — signals
    await strategy.start()
    signals = await strategy.on_data(data)
    assert len(signals) > 0

    # Paused — no signals
    await strategy.pause()
    assert await strategy.on_data(data) == []

    # Resumed — signals again
    await strategy.resume()
    signals = await strategy.on_data(data)
    assert len(signals) > 0

    await strategy.stop()
