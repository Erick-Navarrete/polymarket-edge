"""Arbitrage strategy — internal YES+NO and cross-platform Polymarket/Kalshi."""

import structlog
from decimal import Decimal

from src.core.config import Settings
from src.core.data_feed import DataFeed
from src.core.strategy_base import MarketData, Strategy, TradeSignal

logger = structlog.get_logger()

ONE_DOLLAR = Decimal("1.0")
MIN_PROFIT_THRESHOLD = Decimal("0.005")  # 0.5 cents minimum profit per pair


class ArbitrageStrategy(Strategy):
    """Detect and trade price inefficiencies:

    1. Internal arb: YES price + NO price < $1.00 on the same market
    2. Cross-platform arb: price gap between Polymarket and Kalshi
    """

    def __init__(self, settings: Settings, data_feed: DataFeed) -> None:
        super().__init__(name="arbitrage", settings=settings)
        self.data_feed = data_feed
        self.cross_platform_enabled = bool(settings.kalshi_api_key)
        self._kalshi_prices: dict[str, Decimal] = {}

    async def start(self) -> None:
        await super().start()
        logger.info("arbitrage_started", cross_platform=self.cross_platform_enabled)

    async def on_data(self, data: MarketData) -> list[TradeSignal]:
        if self.state.value != "running":
            return []

        signals: list[TradeSignal] = []

        # Internal arbitrage: YES + NO < $1
        internal = self._check_internal_arb(data)
        if internal:
            signals.extend(internal)

        # Cross-platform arbitrage: Polymarket vs Kalshi
        if self.cross_platform_enabled:
            cross = self._check_cross_platform_arb(data)
            if cross:
                signals.extend(cross)

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

        size = profit_per_pair * Decimal("10")  # Scale position with edge
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

    def _check_cross_platform_arb(self, data: MarketData) -> list[TradeSignal]:
        """Compare Polymarket prices to Kalshi for the same market."""
        kalshi_price = self._kalshi_prices.get(data.condition_id)
        if kalshi_price is None:
            return []

        gap = data.yes_price - kalshi_price
        min_gap = Decimal("0.03")  # 3 cent minimum gap to cover fees

        if abs(gap) < min_gap:
            return []

        signals = []
        if gap > 0:
            # Polymarket YES is overpriced relative to Kalshi
            # Sell YES on PM, Buy on Kalshi
            signals.append(
                TradeSignal(
                    condition_id=data.condition_id,
                    side="SELL_YES",
                    price=data.yes_price,
                    size=Decimal("1"),
                    reason=f"Cross-platform arb: PM YES={data.yes_price} vs Kalshi={kalshi_price}, gap={gap}",
                    confidence=float(gap),
                    strategy=self.name,
                )
            )
        else:
            # Polymarket YES is underpriced relative to Kalshi
            # Buy YES on PM
            signals.append(
                TradeSignal(
                    condition_id=data.condition_id,
                    side="BUY_YES",
                    price=data.yes_price,
                    size=Decimal("1"),
                    reason=f"Cross-platform arb: PM YES={data.yes_price} vs Kalshi={kalshi_price}, gap={gap}",
                    confidence=float(abs(gap)),
                    strategy=self.name,
                )
            )

        return signals

    def update_kalshi_price(self, condition_id: str, price: Decimal) -> None:
        """Update Kalshi price for cross-platform comparison."""
        self._kalshi_prices[condition_id] = price
