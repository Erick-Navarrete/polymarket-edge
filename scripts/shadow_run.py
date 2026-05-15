"""Shadow mode runner — test strategies against live Polymarket data with paper execution.

Connects to the live WebSocket feed, feeds real-time market data to all strategies,
but executes only paper trades. Reports all generated signals and paper P&L.

Usage:
    python scripts/shadow_run.py                     # Run for 5 minutes
    python scripts/shadow_run.py --duration 300      # Run for 5 minutes (seconds)
    python scripts/shadow_run.py --strategy weather   # Only weather strategy
    python scripts/shadow_run.py --top-markets 10    # Subscribe to top 10 markets
    python scripts/shadow_run.py --include-weather   # Also discover weather markets

No live trading — SHADOW_MODE=true always. All trades are paper.
"""

import argparse
import asyncio
import json
import time
from decimal import Decimal

import httpx
import structlog

from src.core.config import Settings
from src.core.data_feed import DataFeed
from src.core.engine import Engine
from src.core.strategy_base import MarketData, TradeSignal
from src.strategies.arbitrage import ArbitrageStrategy
from src.strategies.market_making import MarketMakingStrategy
from src.strategies.copy_trading import CopyTradingStrategy
from src.strategies.crypto_15m import Crypto15mStrategy
from src.strategies.ai_agent import AIAgentStrategy
from src.strategies.weather import WeatherStrategy, WeatherForecast

logger = structlog.get_logger()

GAMMA_API = "https://gamma-api.polymarket.com"

WEATHER_KEYWORDS = ("temperature", "temp", "rain", "snow", "precipitation", "fahrenheit", "weather")


async def discover_active_tokens(top_n: int = 10) -> tuple[list[str], list[dict]]:
    """Fetch top active market token IDs and raw market data from Gamma API.

    Returns (token_ids, markets) so callers can extract metadata without re-fetching.
    """
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(
            f"{GAMMA_API}/markets",
            params={"limit": top_n, "closed": False, "order": "volume", "ascending": False},
        )
        resp.raise_for_status()
        markets = resp.json()

        token_ids = []
        for m in markets:
            raw = m.get("clobTokenIds", "[]")
            ids = json.loads(raw) if isinstance(raw, str) else raw
            if ids:
                token_ids.append(ids[0])  # YES token

        return token_ids, markets


async def discover_weather_tokens(n: int = 5) -> list[tuple[str, str, str]]:
    """Discover weather market tokens from the Gamma /events endpoint.

    Returns list of (token_id, question, condition_id) tuples for weather markets.
    Only includes markets whose question text contains weather-related keywords
    (temperature, rain, snow, etc.) to exclude mis-tagged events like pandemics.
    """
    weather_tokens: list[tuple[str, str, str]] = []
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(
            f"{GAMMA_API}/events",
            params={"limit": 100, "closed": False, "order": "volume", "ascending": False},
        )
        resp.raise_for_status()
        events = resp.json()

        for event in events:
            is_weather = False
            tags = event.get("tags", [])
            if isinstance(tags, list):
                for tag in tags:
                    label = tag.get("label", "") if isinstance(tag, dict) else str(tag)
                    if label in ("Weather", "Daily Temperature", "Wildfire"):
                        is_weather = True
                        break

            series = event.get("series", "") or event.get("seriesSlug", "")
            if isinstance(series, str) and "weather" in series.lower():
                is_weather = True

            if not is_weather:
                continue

            for market in event.get("markets", []):
                raw = market.get("clobTokenIds", "[]")
                ids = json.loads(raw) if isinstance(raw, str) else raw
                question = market.get("question", "")
                condition_id = market.get("conditionId", "")

                # Only include markets that look like actual weather questions
                q_lower = question.lower()
                is_weather_question = any(kw in q_lower for kw in WEATHER_KEYWORDS)
                if not is_weather_question:
                    continue

                if ids:
                    weather_tokens.append((ids[0], question, condition_id))

                if len(weather_tokens) >= n:
                    break

            if len(weather_tokens) >= n:
                break

    return weather_tokens[:n]


def make_forecast_from_question(question: str) -> WeatherForecast | None:
    """Create a WeatherForecast by parsing a temperature question.

    Weather markets on Polymarket follow the pattern:
    "Will the highest temperature in NYC be between 76-77F on May 16?"
    We parse the temperature range and estimate probability from a
    climatological baseline.
    """
    import re
    q = question.lower()

    # Temperature range: "between 76-77F"
    range_match = re.search(r"between\s+(\d+)\s*-\s*(\d+)\s*", q)
    # Single threshold: "exceed 85F"
    exceed_match = re.search(r"(?:exceed|above|higher than)\s+(\d+)\s*", q)

    if range_match:
        low_f = int(range_match.group(1))
        high_f = int(range_match.group(2))
        mid = (low_f + high_f) / 2
    elif exceed_match:
        mid = int(exceed_match.group(1))
    else:
        return None

    # Determine location from question (simplified: default to NYC baseline)
    avg_high = Decimal("72")
    std_dev = Decimal("8")

    if "london" in q:
        avg_high = Decimal("64")
        std_dev = Decimal("6")
    elif "tokyo" in q:
        avg_high = Decimal("76")
        std_dev = Decimal("7")
    elif "austin" in q or "houston" in q or "dallas" in q or "miami" in q:
        avg_high = Decimal("88")
        std_dev = Decimal("8")

    threshold = Decimal(str(mid))
    diff = avg_high - threshold
    z_score = diff / std_dev

    if z_score > Decimal("3"):
        est_prob = Decimal("0.99")
    elif z_score < Decimal("-3"):
        est_prob = Decimal("0.01")
    else:
        est_prob = Decimal("0.5") + z_score * Decimal("0.2")
    est_prob = max(Decimal("0.01"), min(Decimal("0.99"), est_prob))

    return WeatherForecast(
        location="auto-detected",
        high_temp_f=avg_high,
        precipitation_prob=None,
        source="climatology_baseline",
    )


class SignalCollector:
    """Collect all signals and paper fills for reporting."""

    def __init__(self) -> None:
        self.signals: list[dict] = []
        self.fills: list[dict] = []
        self.start_time: float = 0
        self.end_time: float = 0
        self.data_points: int = 0

    def record_signal(self, signal: TradeSignal) -> None:
        self.signals.append({
            "time": time.time(),
            "strategy": signal.strategy,
            "condition_id": signal.condition_id[:16],
            "side": signal.side,
            "price": str(signal.price),
            "size": str(signal.size),
            "reason": signal.reason[:80] if signal.reason else "",
        })

    def record_fill(self, signal: TradeSignal, fill_price: Decimal, fill_size: Decimal) -> None:
        pnl = (signal.price - fill_price) * fill_size
        self.fills.append({
            "time": time.time(),
            "strategy": signal.strategy,
            "side": signal.side,
            "signal_price": str(signal.price),
            "fill_price": str(fill_price),
            "size": str(fill_size),
            "pnl": str(pnl),
        })

    def summary(self) -> dict:
        total_pnl = sum(Decimal(f["pnl"]) for f in self.fills)
        by_strategy: dict[str, dict] = {}
        for f in self.fills:
            s = f["strategy"]
            if s not in by_strategy:
                by_strategy[s] = {"trades": 0, "pnl": Decimal("0"), "wins": 0}
            by_strategy[s]["trades"] += 1
            by_strategy[s]["pnl"] += Decimal(f["pnl"])
            if Decimal(f["pnl"]) > 0:
                by_strategy[s]["wins"] += 1

        elapsed = self.end_time - self.start_time if self.end_time else 0
        return {
            "duration_sec": round(elapsed, 1),
            "data_points": self.data_points,
            "total_signals": len(self.signals),
            "total_fills": len(self.fills),
            "total_pnl": str(total_pnl),
            "by_strategy": {
                k: {
                    "trades": v["trades"],
                    "wins": v["wins"],
                    "win_rate": f"{v['wins'] / v['trades']:.0%}" if v["trades"] > 0 else "N/A",
                    "pnl": str(v["pnl"]),
                }
                for k, v in by_strategy.items()
            },
        }


async def shadow_run(
    duration: int = 300,
    strategy_filter: str | None = None,
    top_markets: int = 10,
    include_weather: bool = False,
) -> SignalCollector:
    """Run the engine in shadow mode for a fixed duration."""
    settings = Settings(live_mode=False, shadow_mode=True)
    collector = SignalCollector()

    # Discover market tokens
    print(f"Discovering top {top_markets} active markets...")
    token_ids, raw_markets = await discover_active_tokens(top_markets)
    print(f"Found {len(token_ids)} tokens to monitor")

    # Discover weather tokens if requested
    weather_tokens: list[tuple[str, str, str]] = []
    if include_weather:
        print("Discovering weather markets...")
        weather_tokens = await discover_weather_tokens(5)
        print(f"Found {len(weather_tokens)} weather tokens")
        weather_token_ids = [t[0] for t in weather_tokens]
        for tid in weather_token_ids:
            if tid not in token_ids:
                token_ids.append(tid)

    if not token_ids:
        print("No active markets found. Exiting.")
        return collector

    # Build engine
    engine = Engine(settings)
    engine._equity = Decimal("1000")

    data_feed = DataFeed(settings)

    strategies = {
        "arbitrage": lambda: ArbitrageStrategy(settings, data_feed),
        "market_making": lambda: MarketMakingStrategy(settings),
        "copy_trading": lambda: CopyTradingStrategy(settings),
        "crypto_15m": lambda: Crypto15mStrategy(settings),
        "ai_agent": lambda: AIAgentStrategy(settings),
        "weather": lambda: WeatherStrategy(settings),
    }

    for name, factory in strategies.items():
        if strategy_filter and name != strategy_filter:
            continue
        engine.add_strategy(factory())

    # Pre-populate weather strategy forecasts for discovered weather markets
    if include_weather and weather_tokens:
        weather_strat = engine._strategies.get("weather")
        if weather_strat:
            for token_id, question, condition_id in weather_tokens:
                forecast = make_forecast_from_question(question)
                if forecast:
                    key = condition_id or token_id
                    weather_strat.update_forecast(key, forecast)
                    if condition_id and condition_id != token_id:
                        weather_strat.update_forecast(token_id, forecast)
                    print(f"  Weather forecast set: {question[:60]}...")

    # Set market metadata from the markets we already fetched (no extra API call)
    for m in raw_markets:
        raw_clob = m.get("clobTokenIds", "[]")
        clob_ids = json.loads(raw_clob) if isinstance(raw_clob, str) else raw_clob
        question = m.get("question", "")
        condition_id = m.get("conditionId", "")
        for tid in clob_ids:
            data_feed.set_metadata(tid, question, condition_id)

    # Also prefetch for weather tokens not in raw_markets
    weather_only_ids = [t[0] for t in weather_tokens if t[0] not in token_ids]
    if weather_only_ids:
        await data_feed.prefetch_metadata(weather_only_ids)
    print(f"Metadata cached for {len(data_feed._token_metadata)} tokens")

    # Monkey-patch engine to collect signals
    async def collecting_process(data: MarketData) -> None:
        collector.data_points += 1
        for strategy in engine._strategies.values():
            try:
                signals = await strategy.on_data(data)
                for signal in signals:
                    collector.record_signal(signal)
                    result = await engine.executor.execute(signal, engine._equity)
                    if result.success:
                        collector.record_fill(signal, result.fill_price, result.fill_size)
                        await strategy.on_fill(signal, result.fill_price, result.fill_size)
            except Exception as e:
                logger.warning("strategy_error", strategy=strategy.name, error=str(e))

    engine._process_market_data = collecting_process

    # Start engine
    print(f"\nStarting shadow mode for {duration}s...")
    print(f"Mode: SHADOW (live data, paper execution)")
    print(f"Strategies: {list(engine._strategies.keys())}")
    print(f"Markets: {len(token_ids)} total")
    print(f"Equity: $1000")
    print("=" * 70)

    await engine.start()
    collector.start_time = time.time()

    # Run market loop with timeout
    try:
        await asyncio.wait_for(
            engine.run_market_loop(token_ids),
            timeout=duration,
        )
    except asyncio.TimeoutError:
        print("\nShadow duration elapsed.")
    except KeyboardInterrupt:
        print("\nInterrupted by user.")

    collector.end_time = time.time()
    await engine.stop()

    return collector


def print_report(collector: SignalCollector) -> None:
    """Print a summary report of the shadow run."""
    s = collector.summary()

    print("\n" + "=" * 70)
    print("Shadow Mode Results")
    print("=" * 70)
    print(f"Duration: {s['duration_sec']}s")
    print(f"Data points: {s['data_points']}")
    print(f"Total signals: {s['total_signals']}")
    print(f"Total paper fills: {s['total_fills']}")
    print(f"Total paper PnL: ${s['total_pnl']}")

    if s["by_strategy"]:
        print(f"\n{'Strategy':<16} {'Trades':>7} {'Wins':>5} {'Win Rate':>9} {'PnL':>12}")
        print("-" * 55)
        for name, stats in sorted(s["by_strategy"].items()):
            print(
                f"{name:<16} "
                f"{stats['trades']:>7} "
                f"{stats['wins']:>5} "
                f"{stats['win_rate']:>9} "
                f"${stats['pnl']:>11}"
            )

    # Show last 10 signals
    if collector.signals:
        print(f"\nLast 10 signals:")
        for sig in collector.signals[-10:]:
            print(
                f"  {sig['strategy']:<12} {sig['side']:<8} "
                f"price={sig['price']:<8} size={sig['size']:<8} "
                f"{sig['reason']}"
            )

    print("=" * 70 + "\n")


async def main(duration: int, strategy: str | None, top_markets: int, include_weather: bool) -> None:
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ],
    )

    collector = await shadow_run(
        duration=duration,
        strategy_filter=strategy,
        top_markets=top_markets,
        include_weather=include_weather,
    )
    print_report(collector)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Shadow mode — live data, paper execution")
    parser.add_argument(
        "--duration", type=int, default=300,
        help="Run duration in seconds (default: 300 = 5 min)",
    )
    parser.add_argument(
        "--strategy", type=str, default=None,
        choices=["arbitrage", "market_making", "copy_trading", "crypto_15m", "ai_agent", "weather"],
        help="Run only one strategy (default: all)",
    )
    parser.add_argument(
        "--top-markets", type=int, default=10,
        help="Number of top-volume markets to subscribe to (default: 10)",
    )
    parser.add_argument(
        "--include-weather", action="store_true",
        help="Also discover and subscribe to weather markets",
    )
    args = parser.parse_args()
    asyncio.run(main(args.duration, args.strategy, args.top_markets, args.include_weather))
