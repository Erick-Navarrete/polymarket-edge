"""Backtest strategies on real Polymarket live data.

Fetches active markets from the public Gamma API, converts them to MarketData,
and runs backtests + walk-forward validation using real prices.

Usage:
    python scripts/run_live_backtest.py                     # All strategies
    python scripts/run_live_backtest.py --strategy weather  # Single strategy
    python scripts/run_live_backtest.py --walk-forward      # Walk-forward mode

No API key needed -- the Gamma API is public and unauthenticated.
"""

import argparse
import asyncio
import json
from decimal import Decimal

import httpx
import structlog

from src.backtesting.harness import BacktestHarness
from src.backtesting.walk_forward import WalkForwardValidator, Crypto15mWFValidator
from src.core.config import Settings
from src.core.strategy_base import MarketData
from src.strategies.arbitrage import ArbitrageStrategy
from src.strategies.market_making import MarketMakingStrategy
from src.strategies.copy_trading import CopyTradingStrategy
from src.strategies.crypto_15m import Crypto15mStrategy
from src.strategies.weather import WeatherStrategy, WeatherForecast

logger = structlog.get_logger()

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"


async def fetch_timeseries_markets(top_n: int = 10) -> dict[str, list[MarketData]]:
    """Fetch time-series data for the top markets by volume.

    Since the public CLOB API doesn't expose historical OHLCV without auth,
    we construct a pseudo-timeseries from the market's price change fields
    (1hr, 1day, 1week price changes + current price).
    """
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(
            f"{GAMMA_API}/markets",
            params={"limit": top_n, "closed": False, "order": "volume", "ascending": False},
        )
        resp.raise_for_status()
        markets = resp.json()

        series: dict[str, list[MarketData]] = {}
        for m in markets:
            try:
                cid = m.get("conditionId", "")
                prices_raw = m.get("outcomePrices")
                if not prices_raw:
                    continue
                prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
                if not prices or len(prices) < 2:
                    continue

                yes = Decimal(str(prices[0]))
                if yes <= 0:
                    continue

                question = m.get("question", "")
                vol = Decimal(str(m.get("volume24hr", m.get("volume", "0"))).replace(",", ""))
                spread = Decimal(str(m.get("spread", "0.02")))

                one_hr = Decimal(str(m.get("oneHourPriceChange", "0") or "0"))
                one_day = Decimal(str(m.get("oneDayPriceChange", "0") or "0"))
                one_wk = Decimal(str(m.get("oneWeekPriceChange", "0") or "0"))

                base_ts = 0.0
                created = m.get("createdAt", "")
                if created:
                    try:
                        from datetime import datetime
                        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                        base_ts = dt.timestamp()
                    except Exception:
                        pass

                data_points = []
                for offset_secs, price_delta in [
                    (7 * 86400, one_wk),
                    (86400, one_day),
                    (3600, one_hr),
                    (0, Decimal("0")),
                ]:
                    adj_yes = max(Decimal("0.01"), min(Decimal("0.99"), yes + price_delta))
                    adj_no = Decimal("1") - adj_yes
                    data_points.append(MarketData(
                        condition_id=cid,
                        question=question,
                        yes_price=adj_yes,
                        no_price=adj_no,
                        spread=spread,
                        volume_24h=vol,
                        timestamp=base_ts - offset_secs if base_ts else 0,
                        raw=m,
                    ))

                series[cid] = data_points
            except Exception:
                continue

    return series


async def fetch_live_markets(limit: int = 200) -> list[MarketData]:
    """Fetch active markets from Gamma API and convert to MarketData."""
    all_markets = []
    offset = 0
    batch_size = 50

    async with httpx.AsyncClient(timeout=60) as client:
        while offset < limit:
            try:
                resp = await client.get(
                    f"{GAMMA_API}/markets",
                    params={
                        "limit": batch_size,
                        "offset": offset,
                        "closed": False,
                        "order": "volume",
                        "ascending": False,
                    },
                )
                resp.raise_for_status()
                batch = resp.json()
                if not batch:
                    break
                all_markets.extend(batch)
                offset += batch_size
                if len(batch) < batch_size:
                    break
            except Exception as e:
                logger.warning("fetch_batch_failed", offset=offset, error=str(e))
                break

    converted = []
    skipped_reasons = {"no_prices": 0, "invalid_price": 0, "parse_error": 0}
    for m in all_markets:
        try:
            prices_raw = m.get("outcomePrices")
            if prices_raw is None:
                skipped_reasons["no_prices"] += 1
                continue
            prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
            if not prices or len(prices) < 2:
                skipped_reasons["no_prices"] += 1
                continue

            yes = Decimal(str(prices[0]))
            no = Decimal(str(prices[1]))

            if yes <= 0 or no <= 0 or (yes + no) <= 0:
                skipped_reasons["invalid_price"] += 1
                continue

            vol = m.get("volume", "0")
            if isinstance(vol, str):
                vol = vol.replace(",", "")
            volume = Decimal(str(vol))

            created = m.get("createdAt", "")
            ts = 0.0
            if created:
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    ts = dt.timestamp()
                except Exception:
                    ts = 0.0

            spread_val = abs(yes - no)
            best_ask = m.get("bestAsk")
            best_bid = m.get("bestBid")
            if best_ask is not None and best_bid is not None:
                spread_val = Decimal(str(best_ask)) - Decimal(str(best_bid))

            converted.append(MarketData(
                condition_id=m.get("conditionId", ""),
                question=m.get("question", ""),
                yes_price=yes,
                no_price=no,
                spread=abs(spread_val),
                volume_24h=volume,
                timestamp=ts,
                raw=m,
            ))
        except Exception:
            skipped_reasons["parse_error"] += 1
            continue

    logger.info("markets_fetched", total=len(all_markets), valid=len(converted), skipped=skipped_reasons)
    return converted


CRYPTO_KEYWORDS = ["btc", "bitcoin", "eth", "ethereum", "crypto", "solana", "xrp"]
WEATHER_KEYWORDS = ["weather", "temperature", "rain", "snow", "hurricane", "tornado", "heat"]
POLITICS_KEYWORDS = ["trump", "biden", "election", "president", "congress", "senate", "supreme court"]
SPORTS_KEYWORDS = [" nfl", "nba", "mlb", "soccer", "f1", "ufc", "ncaa", "premier league"]


def categorize_markets(markets: list[MarketData]) -> dict[str, list[MarketData]]:
    """Sort markets into categories by question keywords."""
    buckets: dict[str, list[MarketData]] = {
        "crypto": [],
        "weather": [],
        "politics": [],
        "sports": [],
        "other": [],
    }
    for m in markets:
        q = m.question.lower()
        if any(kw in q for kw in CRYPTO_KEYWORDS):
            buckets["crypto"].append(m)
        elif any(kw in q for kw in WEATHER_KEYWORDS):
            buckets["weather"].append(m)
        elif any(kw in q for kw in POLITICS_KEYWORDS):
            buckets["politics"].append(m)
        elif any(kw in q for kw in SPORTS_KEYWORDS):
            buckets["sports"].append(m)
        else:
            buckets["other"].append(m)
    return buckets


def make_strategy(name: str, settings: Settings, data: list[MarketData] | None = None):
    """Create a strategy instance by name."""
    if name == "arbitrage":
        from src.core.data_feed import DataFeed
        return ArbitrageStrategy(settings, DataFeed(settings))
    elif name == "market_making":
        return MarketMakingStrategy(settings)
    elif name == "crypto_15m":
        return Crypto15mStrategy(settings)
    elif name == "copy_trading":
        return CopyTradingStrategy(settings)
    elif name == "weather":
        strat = WeatherStrategy(settings)
        if data:
            for d in data[:30]:
                strat.update_forecast(d.condition_id, WeatherForecast(
                    location="NYC",
                    high_temp_f=Decimal("94"),
                    precipitation_prob=Decimal("0.1"),
                ))
        return strat
    raise ValueError(f"Unknown strategy: {name}")


async def run_crosssectional_backtest(
    strategy_data: dict[str, list[MarketData]],
    settings: Settings,
) -> None:
    """Run backtests on cross-sectional market snapshots."""
    print("\n" + "=" * 90)
    print("Cross-Sectional Backtest on Live Polymarket Data")
    print("(Each data point = a different market at current time)")
    print("=" * 90)
    header = f"{'Strategy':<16} {'Data':>6} {'Trades':>7} {'Win Rate':>10} {'PnL':>10} {'Sharpe':>8} {'Drawdown':>10} {'Equity':>10}"
    print(header)
    print("-" * 90)

    for name, data in sorted(strategy_data.items()):
        if len(data) < 5:
            print(f"{name:<16} {len(data):>6}  SKIPPED (need >= 5 data points)")
            continue
        try:
            strategy = make_strategy(name, settings, data)
            harness = BacktestHarness(initial_equity=Decimal("1000"))
            result = await harness.run(strategy, data)
            await strategy.stop()
            s = result.summary()
            print(
                f"{s['strategy']:<16} "
                f"{len(data):>6} "
                f"{s['total_trades']:>7} "
                f"{s['win_rate']:>10} "
                f"${s['total_pnl']:>9} "
                f"{s['sharpe_ratio']:>8} "
                f"{s['max_drawdown']:>9}% "
                f"${s['final_equity']:>9}"
            )
        except Exception as e:
            print(f"{name:<16} ERROR: {e}")

    print("=" * 90 + "\n")


async def run_crosssectional_wf(
    strategy_data: dict[str, list[MarketData]],
    settings: Settings,
) -> None:
    """Run walk-forward on cross-sectional data."""
    print("\n" + "=" * 90)
    print("Walk-Forward Validation on Live Data")
    print("=" * 90)
    header = f"{'Strategy':<16} {'Data':>6} {'Windows':>8} {'Trades':>7} {'OOS Sharpe':>10} {'OOS Win%':>9} {'Degr':>6} {'Risk':>8}"
    print(header)
    print("-" * 90)

    for name, data in sorted(strategy_data.items()):
        if len(data) < 20:
            print(f"{name:<16} {len(data):>6}  SKIPPED (need >= 20)")
            continue
        try:
            strategy = make_strategy(name, settings, data)
            if name == "crypto_15m":
                validator = Crypto15mWFValidator(initial_equity=Decimal("1000"))
            else:
                validator = WalkForwardValidator(initial_equity=Decimal("1000"))
            await strategy.start()
            result = await validator.validate(strategy, data)
            await strategy.stop()
            s = result.summary()
            print(
                f"{s['strategy']:<16} "
                f"{len(data):>6} "
                f"{s['num_windows']:>8} "
                f"{s['total_test_trades']:>7} "
                f"{s['avg_test_sharpe']:>10} "
                f"{s['avg_test_win_rate']:>9} "
                f"{s['avg_sharpe_degradation']:>6} "
                f"{s['overfitting_risk']:>8}"
            )
        except Exception as e:
            print(f"{name:<16} ERROR: {e}")

    print("=" * 90 + "\n")


async def run_timeseries_backtest(settings: Settings) -> None:
    """Run timeseries backtests on top markets using price-change pseudo-history."""
    print("\nFetching timeseries data for top markets...")
    series = await fetch_timeseries_markets(top_n=20)

    if not series:
        print("No timeseries data available.")
        return

    all_points = []
    for points in series.values():
        all_points.extend(points)
    all_points.sort(key=lambda d: d.timestamp)

    n_markets = len(series)
    n_per = len(all_points) // max(n_markets, 1)
    print(f"Got {n_markets} markets x {n_per} snapshots = {len(all_points)} total points")

    results = []
    for strat_name in ["crypto_15m", "market_making"]:
        strategy = make_strategy(strat_name, settings)
        harness = BacktestHarness(initial_equity=Decimal("1000"))
        result = await harness.run(strategy, all_points)
        await strategy.stop()
        results.append(result.summary())

    print("\n" + "=" * 90)
    print("Timeseries Backtest (pseudo-history from price changes)")
    print("(Sequential price snapshots for same markets over time)")
    print("=" * 90)
    header = f"{'Strategy':<16} {'Trades':>7} {'Win Rate':>10} {'PnL':>10} {'Sharpe':>8} {'Drawdown':>10} {'Equity':>10}"
    print(header)
    print("-" * 90)
    for r in results:
        print(
            f"{r['strategy']:<16} "
            f"{r['total_trades']:>7} "
            f"{r['win_rate']:>10} "
            f"${r['total_pnl']:>9} "
            f"{r['sharpe_ratio']:>8} "
            f"{r['max_drawdown']:>9}% "
            f"${r['final_equity']:>9}"
        )
    print("=" * 90 + "\n")


async def main(strategy_filter: str | None = None, walk_forward: bool = False) -> None:
    settings = Settings(live_mode=False)

    print("\nFetching live market data from Polymarket...")
    all_data = await fetch_live_markets(limit=200)
    buckets = categorize_markets(all_data)

    print(f"Fetched {len(all_data)} markets:")
    for cat, items in buckets.items():
        print(f"  {cat}: {len(items)}")

    # Cross-sectional: each data point is a different market at one time.
    # Best for: arbitrage, weather (evaluate each market independently)
    # Timeseries: same market over time. Best for: crypto_15m, market_making
    strategy_data = {
        "arbitrage": all_data,
        "market_making": all_data,
        "crypto_15m": buckets["crypto"] + buckets["other"],
        "copy_trading": all_data,
        "weather": buckets["weather"] + buckets["other"],
    }

    if strategy_filter:
        strategy_data = {k: v for k, v in strategy_data.items() if k == strategy_filter}

    if walk_forward:
        await run_crosssectional_wf(strategy_data, settings)
    else:
        await run_crosssectional_backtest(strategy_data, settings)

    # Also run timeseries backtests (always, since this data is complementary)
    await run_timeseries_backtest(settings)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest on live Polymarket data")
    parser.add_argument(
        "--strategy", type=str, default=None,
        choices=["arbitrage", "market_making", "crypto_15m", "copy_trading", "weather"],
        help="Run a specific strategy (default: all)",
    )
    parser.add_argument(
        "--walk-forward", action="store_true",
        help="Run walk-forward validation instead of simple backtest",
    )
    args = parser.parse_args()
    asyncio.run(main(args.strategy, walk_forward=args.walk_forward))
