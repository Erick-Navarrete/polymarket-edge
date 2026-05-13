"""Run backtests for all strategies using synthetic historical data.

Usage:
    python scripts/run_backtest.py                   # Run all strategies
    python scripts/run_backtest.py --strategy arbitrage  # Run one strategy

This script generates representative synthetic market data that exercises
each strategy's logic, then runs them through the backtest harness and
prints a performance summary table.

For real historical data, set POLYMARKET_API_KEY in .env and use
--live-data flag (requires API access).
"""

import argparse
import asyncio
from decimal import Decimal

from src.backtesting.harness import BacktestHarness
from src.backtesting.walk_forward import WalkForwardValidator, Crypto15mWFValidator
from src.core.config import Settings
from src.core.data_feed import DataFeed
from src.core.strategy_base import MarketData
from src.strategies.arbitrage import ArbitrageStrategy
from src.strategies.market_making import MarketMakingStrategy
from src.strategies.copy_trading import CopyTradingStrategy
from src.strategies.crypto_15m import Crypto15mStrategy
from src.strategies.weather import WeatherStrategy, WeatherForecast


def generate_arb_data(n: int = 200) -> list[MarketData]:
    """Generate market data with periodic internal arb opportunities."""
    data = []
    for i in range(n):
        # ~20% of the time, YES+NO < $1 (arb opportunity)
        if i % 5 == 0:
            yes = Decimal("0.42") + Decimal(str(i % 7)) * Decimal("0.01")
            no = Decimal("0.48") + Decimal(str(i % 5)) * Decimal("0.01")
        else:
            yes = Decimal("0.50") + Decimal(str(i % 11 - 5)) * Decimal("0.01")
            no = Decimal("1") - yes
        data.append(MarketData(
            condition_id="0xarb_market",
            question="Will X happen?",
            yes_price=yes,
            no_price=no,
            spread=abs(yes - no),
            volume_24h=Decimal("10000"),
            timestamp=1000.0 + i * 60,
        ))
    return data


def generate_mm_data(n: int = 200) -> list[MarketData]:
    """Generate mean-reverting market data suitable for market making."""
    data = []
    mid = Decimal("0.50")
    for i in range(n):
        # Random walk around 0.50
        step = Decimal(str(((i * 7 + 3) % 5 - 2))) * Decimal("0.005")
        mid = max(Decimal("0.30"), min(Decimal("0.70"), mid + step))
        spread = Decimal("0.02") + Decimal(str(i % 3)) * Decimal("0.01")
        data.append(MarketData(
            condition_id="0xmm_market",
            question="Will Y happen?",
            yes_price=mid,
            no_price=Decimal("1") - mid,
            spread=spread,
            volume_24h=Decimal("5000"),
            timestamp=1000.0 + i * 60,
        ))
    return data


def generate_crypto_data(n: int = 200) -> list[MarketData]:
    """Generate volatile price data simulating 15-min BTC markets."""
    data = []
    price = Decimal("0.50")
    for i in range(n):
        # More volatile moves
        step = Decimal(str(((i * 13 + 7) % 9 - 4))) * Decimal("0.02")
        price = max(Decimal("0.05"), min(Decimal("0.95"), price + step))
        # Occasional spikes
        if i % 15 == 0:
            price = max(Decimal("0.05"), min(Decimal("0.95"), price + Decimal("0.10")))
        data.append(MarketData(
            condition_id="0xbtc_15m",
            question="Will BTC be above $70k at 3pm?",
            yes_price=price,
            no_price=Decimal("1") - price,
            spread=Decimal("0.01"),
            volume_24h=Decimal("50000"),
            timestamp=1000.0 + i * 900,  # 15-min intervals
        ))
    return data


def generate_weather_data(n: int = 100) -> list[MarketData]:
    """Generate weather market data."""
    data = []
    for i in range(n):
        yes = Decimal("0.60") + Decimal(str(i % 7 - 3)) * Decimal("0.05")
        yes = max(Decimal("0.10"), min(Decimal("0.90"), yes))
        data.append(MarketData(
            condition_id="0xweather_nyc",
            question="Will NYC high temp exceed 90F on July 4?",
            yes_price=yes,
            no_price=Decimal("1") - yes,
            spread=Decimal("0.03"),
            volume_24h=Decimal("2000"),
            timestamp=1000.0 + i * 3600,
        ))
    return data


def generate_copy_data(n: int = 100) -> list[MarketData]:
    """Generate generic market data for copy trading (signals driven by wallet activity, not price)."""
    data = []
    for i in range(n):
        yes = Decimal("0.50") + Decimal(str(i % 9 - 4)) * Decimal("0.02")
        data.append(MarketData(
            condition_id="0xcopy_market",
            question="Will Z happen by end of month?",
            yes_price=max(Decimal("0.10"), min(Decimal("0.90"), yes)),
            no_price=max(Decimal("0.10"), min(Decimal("0.90"), Decimal("1") - yes)),
            spread=Decimal("0.02"),
            volume_24h=Decimal("3000"),
            timestamp=1000.0 + i * 300,
        ))
    return data


STRATEGIES = {
    "arbitrage": ("Internal + cross-platform arb", generate_arb_data),
    "market_making": ("Bands strategy on CLOB", generate_mm_data),
    "crypto_15m": ("BTC/ETH 15-min multi-signal fusion", generate_crypto_data),
    "copy_trading": ("On-chain wallet follower", generate_copy_data),
    "weather": ("NOAA forecast vs PM prices", generate_weather_data),
}


async def run_backtest(strategy_name: str, settings: Settings) -> dict:
    """Run a single strategy backtest and return summary."""
    harness = BacktestHarness(initial_equity=Decimal("1000"))
    desc, data_gen = STRATEGIES[strategy_name]
    data = data_gen()

    if strategy_name == "arbitrage":
        strategy = ArbitrageStrategy(settings, DataFeed(settings))
    elif strategy_name == "market_making":
        strategy = MarketMakingStrategy(settings)
    elif strategy_name == "crypto_15m":
        strategy = Crypto15mStrategy(settings)
    elif strategy_name == "copy_trading":
        strategy = CopyTradingStrategy(settings)
    elif strategy_name == "weather":
        strategy = WeatherStrategy(settings)
        # Update forecasts for some data points to trigger signals
        for d in data[:50]:
            strategy.update_forecast(d.condition_id, WeatherForecast(
                location="NYC",
                high_temp_f=Decimal("94"),
                precipitation_prob=Decimal("0.1"),
            ))
    else:
        raise ValueError(f"Unknown strategy: {strategy_name}")

    result = await harness.run(strategy, data, fill_model="midpoint")

    # Reset strategy state for clean output
    await strategy.stop()

    return result.summary()


async def run_walk_forward(strategy_name: str, settings: Settings) -> dict:
    """Run walk-forward validation for a strategy."""
    desc, data_gen = STRATEGIES[strategy_name]
    # Use more data for walk-forward (needs enough for train + test splits)
    data = data_gen()

    if strategy_name == "crypto_15m":
        strategy = Crypto15mStrategy(settings)
        validator = Crypto15mWFValidator(initial_equity=Decimal("1000"))
    else:
        if strategy_name == "arbitrage":
            strategy = ArbitrageStrategy(settings, DataFeed(settings))
        elif strategy_name == "market_making":
            strategy = MarketMakingStrategy(settings)
        elif strategy_name == "weather":
            strategy = WeatherStrategy(settings)
            for d in data[:50]:
                strategy.update_forecast(d.condition_id, WeatherForecast(
                    location="NYC", high_temp_f=Decimal("94"),
                    precipitation_prob=Decimal("0.1"),
                ))
        elif strategy_name == "copy_trading":
            strategy = CopyTradingStrategy(settings)
        else:
            raise ValueError(f"Unknown strategy: {strategy_name}")
        validator = WalkForwardValidator(initial_equity=Decimal("1000"))

    await strategy.start()
    result = await validator.validate(strategy, data)
    await strategy.stop()
    return result.summary()


async def main(strategy_filter: str | None = None, walk_forward: bool = False) -> None:
    settings = Settings(live_mode=False)

    targets = {strategy_filter} if strategy_filter else set(STRATEGIES.keys())

    if walk_forward:
        print("\n" + "=" * 80)
        print("Polymarket Edge — Walk-Forward Validation")
        print("=" * 80)
        print(f"{'Strategy':<20} {'Windows':>8} {'Trades':>7} {'OOS Sharpe':>10} {'OOS Win%':>9} {'Sharpe Degr':>11} {'OOS PnL':>10} {'Risk':>8}")
        print("-" * 80)

        for name in sorted(targets):
            if name not in STRATEGIES:
                print(f"Unknown strategy: {name}")
                continue
            try:
                result = await run_walk_forward(name, settings)
                print(
                    f"{result['strategy']:<20} "
                    f"{result['num_windows']:>8} "
                    f"{result['total_test_trades']:>7} "
                    f"{result['avg_test_sharpe']:>10} "
                    f"{result['avg_test_win_rate']:>9} "
                    f"{result['avg_sharpe_degradation']:>11} "
                    f"${result['total_test_pnl']:>9} "
                    f"{result['overfitting_risk']:>8}"
                )
            except Exception as e:
                print(f"{name:<20} ERROR: {e}")

        print("=" * 80 + "\n")
        return

    print("\n" + "=" * 80)
    print("Polymarket Edge — Backtest Results")
    print("=" * 80)
    print(f"{'Strategy':<20} {'Trades':>7} {'Win Rate':>10} {'PnL':>10} {'Sharpe':>8} {'Drawdown':>10} {'Equity':>10}")
    print("-" * 80)

    for name in sorted(targets):
        if name not in STRATEGIES:
            print(f"Unknown strategy: {name}")
            continue

        try:
            result = await run_backtest(name, settings)
            print(
                f"{result['strategy']:<20} "
                f"{result['total_trades']:>7} "
                f"{result['win_rate']:>10} "
                f"${result['total_pnl']:>9} "
                f"{result['sharpe_ratio']:>8} "
                f"{result['max_drawdown']:>9}% "
                f"${result['final_equity']:>9}"
            )
        except Exception as e:
            print(f"{name:<20} ERROR: {e}")

    print("=" * 80 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Polymarket Edge backtests")
    parser.add_argument(
        "--strategy",
        type=str,
        default=None,
        choices=list(STRATEGIES.keys()),
        help="Run a specific strategy (default: all)",
    )
    parser.add_argument(
        "--walk-forward",
        action="store_true",
        help="Run walk-forward validation instead of simple backtest",
    )
    args = parser.parse_args()
    asyncio.run(main(args.strategy, walk_forward=args.walk_forward))
