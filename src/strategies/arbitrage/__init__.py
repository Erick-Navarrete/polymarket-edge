"""Arbitrage strategy -- internal YES+NO, cross-platform, and mean-reversion arb."""

import time
import structlog
from decimal import Decimal

from src.core.config import Settings
from src.core.data_feed import DataFeed
from src.core.strategy_base import MarketData, Strategy, TradeSignal

logger = structlog.get_logger()

ONE_DOLLAR = Decimal("1.0")
MIN_PROFIT_THRESHOLD = Decimal("0.005")
MIN_MEAN_REVERSION_EDGE = Decimal("0.10")  # 10c deviation (tuned: 5c was too noisy on cheap markets)
CROSS_PLATFORM_MIN_GAP = Decimal("0.03")


class ArbitrageStrategy(Strategy):
    """Detect and trade price inefficiencies:

    1. Internal arb: YES price + NO price < $1.00 on the same market
    2. Cross-platform arb: price gap between Polymarket and Kalshi
    3. Mean-reversion arb: prices far from 50c that should converge
    """

    def __init__(self, settings: Settings, data_feed: DataFeed) -> None:
        super().__init__(name="arbitrage", settings=settings)
        self.data_feed = data_feed
        self.cross_platform_enabled = bool(settings.kalshi_api_key)
        self._kalshi_prices: dict[str, Decimal] = {}
        self._seen_markets: dict[str, dict] = {}
        self._last_signal_time: dict[str, float] = {}
        self._signal_cooldown: float = 60.0

    async def start(self) -> None:
        await super().start()
        logger.info("arbitrage_started", cross_platform=self.cross_platform_enabled)

    async def on_data(self, data: MarketData) -> list[TradeSignal]:
        if self.state.value != "running":
            return []

        now = time.monotonic()
        last = self._last_signal_time.get(data.condition_id, 0)
        if now - last < self._signal_cooldown:
            return []

        signals: list[TradeSignal] = []

        internal = self._check_internal_arb(data)
        if internal:
            signals.extend(internal)

        if not signals:
            mr = self._check_mean_reversion(data)
            if mr:
                signals.append(mr)

        if not signals and self.cross_platform_enabled:
            cross = self._check_cross_platform_arb(data)
            if cross:
                signals.extend(cross)

        if signals:
            self._last_signal_time[data.condition_id] = now

        return signals

    async def on_fill(self, signal: TradeSignal, fill_price: Decimal, fill_size: Decimal) -> None:
        self._total_pnl += (signal.price - fill_price) * fill_size
        logger.info(
            "arbitrage_fill",
            condition_id=signal.condition_id,
            side=signal.side,
            fill_price=str(fill_price),
            fill_size=str(fill_size),
        )

    def _check_internal_arb(self, data: MarketData) -> list[TradeSignal]:
        """If YES + NO < $1, buy both sides for guaranteed profit."""
        total = data.yes_price + data.no_price
        if total >= ONE_DOLLAR:
            return []

        profit_per_pair = ONE_DOLLAR - total
        if profit_per_pair < MIN_PROFIT_THRESHOLD:
            return []

        logger.info(
            "internal_arb_detected",
            condition_id=data.condition_id,
            yes=str(data.yes_price),
            no=str(data.no_price),
            total=str(total),
            profit=str(profit_per_pair),
        )

        size = profit_per_pair * Decimal("10")
        return [
            TradeSignal(
                condition_id=data.condition_id,
                side="BUY_YES",
                price=data.yes_price,
                size=size,
                reason=f"Internal arb: YES+NO={total}, profit={profit_per_pair}/pair",
                confidence=float(profit_per_pair / ONE_DOLLAR),
                strategy=self.name,
            ),
            TradeSignal(
                condition_id=data.condition_id,
                side="BUY_NO",
                price=data.no_price,
                size=size,
                reason=f"Internal arb: YES+NO={total}, profit={profit_per_pair}/pair",
                confidence=float(profit_per_pair / ONE_DOLLAR),
                strategy=self.name,
            ),
        ]

    def _check_mean_reversion(self, data: MarketData) -> TradeSignal | None:
        """Detect extreme prices that should mean-revert.

        Tuned: extreme thresholds 0.03/0.97 (was 0.05/0.95), swing
        lookback 10 ticks (was 5), edge threshold 10c (was 5c).
        """
        cid = data.condition_id
        if cid not in self._seen_markets:
            self._seen_markets[cid] = {"prices": [], "count": 0}
        market = self._seen_markets[cid]
        market["prices"].append(data.yes_price)
        market["count"] += 1

        if market["count"] < 3:
            return None

        market["prices"] = market["prices"][-20:]

        if data.yes_price >= Decimal("0.97"):
            return TradeSignal(
                condition_id=cid,
                side="BUY_NO",
                price=data.no_price,
                size=Decimal("1"),
                reason=f"Mean-reversion arb: YES={data.yes_price} seems overpriced",
                confidence=float(ONE_DOLLAR - data.yes_price),
                strategy=self.name,
            )
        elif data.yes_price <= Decimal("0.03"):
            return TradeSignal(
                condition_id=cid,
                side="BUY_YES",
                price=data.yes_price,
                size=Decimal("1"),
                reason=f"Mean-reversion arb: YES={data.yes_price} seems underpriced",
                confidence=float(data.yes_price),
                strategy=self.name,
            )

        # 10-tick swing detection with tighter edge threshold
        if len(market["prices"]) >= 10:
            recent = market["prices"][-10:]
            avg = sum(recent) / Decimal(str(len(recent)))
            deviation = abs(data.yes_price - avg)
            if deviation >= MIN_MEAN_REVERSION_EDGE:
                if data.yes_price > avg:
                    side = "BUY_NO"
                    price = data.no_price
                else:
                    side = "BUY_YES"
                    price = data.yes_price
                return TradeSignal(
                    condition_id=cid,
                    side=side,
                    price=price,
                    size=Decimal("1"),
                    reason=f"Mean-reversion arb: YES={data.yes_price} vs 10-tick avg={avg:.4f}, deviation={deviation:.4f}",
                    confidence=float(min(deviation / Decimal("0.10"), Decimal("0.7"))),
                    strategy=self.name,
                )

        return None

    def _check_cross_platform_arb(self, data: MarketData) -> list[TradeSignal]:
        """Compare Polymarket prices to Kalshi for the same market."""
        kalshi_price = self._kalshi_prices.get(data.condition_id)
        if kalshi_price is None:
            return []

        gap = data.yes_price - kalshi_price
        if abs(gap) < CROSS_PLATFORM_MIN_GAP:
            return []

        signals = []
        if gap > 0:
            signals.append(TradeSignal(
                condition_id=data.condition_id,
                side="SELL_YES",
                price=data.yes_price,
                size=Decimal("1"),
                reason=f"Cross-platform arb: PM YES={data.yes_price} vs Kalshi={kalshi_price}, gap={gap}",
                confidence=float(gap),
                strategy=self.name,
            ))
        else:
            signals.append(TradeSignal(
                condition_id=data.condition_id,
                side="BUY_YES",
                price=data.yes_price,
                size=Decimal("1"),
                reason=f"Cross-platform arb: PM YES={data.yes_price} vs Kalshi={kalshi_price}, gap={gap}",
                confidence=float(abs(gap)),
                strategy=self.name,
            ))
        return signals

    def update_kalshi_price(self, condition_id: str, price: Decimal) -> None:
        """Update Kalshi price for cross-platform comparison."""
        self._kalshi_prices[condition_id] = price
