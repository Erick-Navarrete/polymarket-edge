"""Market making strategy — bands strategy on the Polymarket CLOB."""

import time
import structlog
from decimal import Decimal

from src.core.config import Settings
from src.core.strategy_base import MarketData, Strategy, TradeSignal

logger = structlog.get_logger()


class BandsConfig:
    """Configuration for the bands market-making strategy."""

    def __init__(
        self,
        spread_bps: int = 200,  # 2% spread (200 basis points)
        order_size: Decimal = Decimal("10"),
        max_position: Decimal = Decimal("100"),
        recenter_speed: Decimal = Decimal("0.5"),  # How fast to adjust to midpoint moves
        min_spread: Decimal = Decimal("0.01"),  # Minimum 1 cent spread
        signal_cooldown_seconds: float = 15.0,  # Min time between signals per market
    ) -> None:
        self.spread_bps = spread_bps
        self.order_size = order_size
        self.max_position = max_position
        self.recenter_speed = recenter_speed
        self.min_spread = min_spread
        self.signal_cooldown_seconds = signal_cooldown_seconds

        # Derived
        self.half_spread = Decimal(spread_bps) / Decimal("20000")  # Convert bps to decimal


class MarketMakingStrategy(Strategy):
    """Place quotes on both sides of the book around the midpoint.

    The bands strategy places a BUY at (midpoint - half_spread) and
    a SELL at (midpoint + half_spread). Position management adjusts
    quotes away from the midpoint when inventory builds up.
    """

    def __init__(self, settings: Settings, config: BandsConfig | None = None) -> None:
        super().__init__(name="market_making", settings=settings)
        self.config = config or BandsConfig()
        self._inventory: dict[str, Decimal] = {}  # condition_id -> net position
        self._last_midpoint: dict[str, Decimal] = {}
        self._last_signal_time: dict[str, float] = {}  # condition_id -> timestamp

    async def start(self) -> None:
        await super().start()
        logger.info(
            "market_making_started",
            spread_bps=self.config.spread_bps,
            order_size=str(self.config.order_size),
            cooldown=self.config.signal_cooldown_seconds,
        )

    async def on_data(self, data: MarketData) -> list[TradeSignal]:
        if self.state.value != "running":
            return []

        midpoint = (data.yes_price + data.no_price) / Decimal("2")
        self._last_midpoint[data.condition_id] = midpoint

        # Cooldown: skip if we signaled for this market too recently
        now = time.monotonic()
        last = self._last_signal_time.get(data.condition_id, 0)
        if now - last < self.config.signal_cooldown_seconds:
            return []

        # Skip if spread is too thin (no room for our quotes)
        if data.spread < self.config.min_spread:
            return []

        inventory = self._inventory.get(data.condition_id, Decimal("0"))

        # Inventory skew: shift quotes away from midpoint to reduce position
        skew = self._calculate_skew(inventory)

        buy_price = midpoint - self.config.half_spread - skew
        sell_price = midpoint + self.config.half_spread - skew

        # Clamp prices to [0.01, 0.99]
        buy_price = max(Decimal("0.01"), min(Decimal("0.99"), buy_price))
        sell_price = max(Decimal("0.01"), min(Decimal("0.99"), sell_price))

        # Don't place both sides if we're at max position
        size = self.config.order_size
        signals = []

        if inventory < self.config.max_position:
            signals.append(
                TradeSignal(
                    condition_id=data.condition_id,
                    side="BUY_YES",
                    price=buy_price,
                    size=size,
                    reason=f"MM bid: mid={midpoint}, skew={skew}, inv={inventory}",
                    confidence=0.5,
                    strategy=self.name,
                )
            )

        if inventory > -self.config.max_position:
            signals.append(
                TradeSignal(
                    condition_id=data.condition_id,
                    side="SELL_YES",
                    price=sell_price,
                    size=size,
                    reason=f"MM ask: mid={midpoint}, skew={skew}, inv={inventory}",
                    confidence=0.5,
                    strategy=self.name,
                )
            )

        if signals:
            self._last_signal_time[data.condition_id] = now

        return signals

    async def on_fill(self, signal: TradeSignal, fill_price: Decimal, fill_size: Decimal) -> None:
        condition_id = signal.condition_id
        current = self._inventory.get(condition_id, Decimal("0"))

        if signal.side == "BUY_YES":
            self._inventory[condition_id] = current + fill_size
            self._total_pnl -= fill_price * fill_size
        elif signal.side == "SELL_YES":
            self._inventory[condition_id] = current - fill_size
            self._total_pnl += fill_price * fill_size

        logger.info(
            "mm_fill",
            condition_id=condition_id,
            side=signal.side,
            fill_price=str(fill_price),
            fill_size=str(fill_size),
            inventory=str(self._inventory[condition_id]),
        )

    def _calculate_skew(self, inventory: Decimal) -> Decimal:
        """Calculate quote skew based on current inventory.

        Positive inventory (long) -> shift quotes down to encourage selling
        Negative inventory (short) -> shift quotes up to encourage buying
        """
        if self.config.max_position == 0:
            return Decimal("0")

        utilization = inventory / self.config.max_position
        # Scale skew by recenter_speed (0 = no adjustment, 1 = full offset)
        return utilization * self.config.recenter_speed * self.config.half_spread * 2
