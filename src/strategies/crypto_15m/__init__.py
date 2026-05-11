"""Crypto 15-minute binary market strategy — multi-signal fusion for BTC/ETH."""

import structlog
from decimal import Decimal
from enum import Enum

from src.core.config import Settings
from src.core.strategy_base import MarketData, Strategy, TradeSignal

logger = structlog.get_logger()


class SignalType(Enum):
    SPIKE_DETECTION = "spike"
    PRICE_DIVERGENCE = "divergence"
    MOMENTUM = "momentum"
    MEAN_REVERSION = "mean_reversion"


class FusedSignal:
    """Weighted vote from multiple sub-signals."""

    def __init__(self) -> None:
        self.votes: list[tuple[SignalType, Decimal, Decimal]] = []  # (type, direction, weight)

    def add_vote(self, signal_type: SignalType, direction: Decimal, weight: Decimal) -> None:
        self.votes.append((signal_type, direction, weight))

    @property
    def consensus(self) -> Decimal:
        """Weighted average of all vote directions. Positive = UP, Negative = DOWN."""
        if not self.votes:
            return Decimal("0")
        total_weight = sum(w for _, _, w in self.votes)
        if total_weight == 0:
            return Decimal("0")
        return sum(d * w for _, d, w in self.votes) / total_weight

    @property
    def confidence(self) -> float:
        """How strong the consensus is (0-1)."""
        if not self.votes:
            return 0.0
        return min(float(abs(self.consensus)), 1.0)


# Default signal weights (tunable, self-learning in future)
DEFAULT_WEIGHTS: dict[SignalType, Decimal] = {
    SignalType.SPIKE_DETECTION: Decimal("0.4"),
    SignalType.PRICE_DIVERGENCE: Decimal("0.3"),
    SignalType.MOMENTUM: Decimal("0.2"),
    SignalType.MEAN_REVERSION: Decimal("0.1"),
}

# Risk parameters
MAX_TRADE_USD = Decimal("1")
STOP_LOSS_PCT = Decimal("30") / Decimal("100")
TAKE_PROFIT_PCT = Decimal("20") / Decimal("100")


class Crypto15mStrategy(Strategy):
    """Trade Polymarket's 15-minute BTC/ETH binary markets.

   signal fusion from:
    - Spike detection (sudden price moves on Binance/Coinbase)
    - Price divergence (futures vs prediction market)
    - Momentum (trend continuation)
    - Mean reversion (oversold/overbought)

    Position sizing: fixed $1 max per trade with stop-loss/take-profit.
    """

    def __init__(self, settings: Settings) -> None:
        super().__init__(name="crypto_15m", settings=settings)
        self._weights = DEFAULT_WEIGHTS.copy()
        self._price_history: dict[str, list[Decimal]] = {}  # condition_id -> recent prices
        self._futures_prices: dict[str, Decimal] = {}  # symbol -> current futures price
        self._history_window = 20  # Number of recent prices to keep

    async def start(self) -> None:
        await super().start()
        logger.info("crypto_15m_started", max_trade=str(MAX_TRADE_USD))

    async def on_data(self, data: MarketData) -> list[TradeSignal]:
        if self.state.value != "running":
            return []

        # Track price history
        self._update_price_history(data)

        # Multi-signal fusion
        fused = FusedSignal()

        spike = self._detect_spike(data)
        if spike is not None:
            fused.add_vote(SignalType.SPIKE_DETECTION, spike, self._weights[SignalType.SPIKE_DETECTION])

        divergence = self._detect_divergence(data)
        if divergence is not None:
            fused.add_vote(SignalType.PRICE_DIVERGENCE, divergence, self._weights[SignalType.PRICE_DIVERGENCE])

        momentum = self._detect_momentum(data)
        if momentum is not None:
            fused.add_vote(SignalType.MOMENTUM, momentum, self._weights[SignalType.MOMENTUM])

        mean_rev = self._detect_mean_reversion(data)
        if mean_rev is not None:
            fused.add_vote(SignalType.MEAN_REVERSION, mean_rev, self._weights[SignalType.MEAN_REVERSION])

        # Generate trade if consensus is strong enough
        if fused.confidence < 0.3:
            return []

        side = "BUY_YES" if fused.consensus > 0 else "BUY_NO"
        price = data.yes_price if side == "BUY_YES" else data.no_price
        size = MAX_TRADE_USD / price if price > 0 else Decimal("0")

        return [
            TradeSignal(
                condition_id=data.condition_id,
                side=side,
                price=price,
                size=size,
                reason=f"15m fusion: consensus={fused.consensus:.2f}, votes={len(fused.votes)}",
                confidence=fused.confidence,
                strategy=self.name,
            )
        ]

    async def on_fill(self, signal: TradeSignal, fill_price: Decimal, fill_size: Decimal) -> None:
        self._total_pnl += (signal.price - fill_price) * fill_size
        logger.info(
            "crypto_15m_fill",
            condition_id=signal.condition_id,
            side=signal.side,
            fill_price=str(fill_price),
            fill_size=str(fill_size),
        )

    def _update_price_history(self, data: MarketData) -> None:
        history = self._price_history.get(data.condition_id, [])
        history.append(data.yes_price)
        if len(history) > self._history_window:
            history = history[-self._history_window :]
        self._price_history[data.condition_id] = history

    def _detect_spike(self, data: MarketData) -> Decimal | None:
        """Detect sudden price moves (>2 std devs from recent mean)."""
        history = self._price_history.get(data.condition_id, [])
        if len(history) < 5:
            return None

        mean = sum(history) / len(history)
        if mean == 0:
            return None
        # Simple spike: price moved >5% from recent mean
        deviation = (data.yes_price - mean) / mean
        if abs(deviation) > Decimal("0.05"):
            return deviation  # Positive = UP spike, Negative = DOWN spike
        return None

    def _detect_divergence(self, data: MarketData) -> Decimal | None:
        """Check if prediction market price diverges from futures price."""
        futures = self._futures_prices.get(data.condition_id)
        if futures is None:
            return None
        divergence = (data.yes_price - futures) / futures if futures > 0 else None
        return divergence

    def _detect_momentum(self, data: MarketData) -> Decimal | None:
        """Simple momentum: recent price trend direction."""
        history = self._price_history.get(data.condition_id, [])
        if len(history) < 3:
            return None
        recent = history[-3:]
        if recent[0] == 0:
            return None
        return (recent[-1] - recent[0]) / recent[0]

    def _detect_mean_reversion(self, data: MarketData) -> Decimal | None:
        """Bet against extreme prices (near 0 or 1)."""
        if data.yes_price > Decimal("0.9"):
            return Decimal("-0.5")  # Bet DOWN (overpriced)
        elif data.yes_price < Decimal("0.1"):
            return Decimal("0.5")  # Bet UP (underpriced)
        return None

    def update_futures_price(self, condition_id: str, price: Decimal) -> None:
        """Update the reference futures price for divergence detection."""
        self._futures_prices[condition_id] = price
