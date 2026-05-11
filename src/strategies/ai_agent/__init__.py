"""AI/LLM agent strategy — news parsing + probability estimation -> Kelly-sized trades."""

import structlog
from decimal import Decimal
from typing import Any

from src.core.config import Settings
from src.core.strategy_base import MarketData, Strategy, TradeSignal

logger = structlog.get_logger()


class ProbabilityEstimate:
    """LLM-generated probability estimate for a market."""

    def __init__(
        self,
        condition_id: str,
        estimated_prob: Decimal,
        confidence: Decimal,
        reasoning: str,
        sources: list[str] | None = None,
    ) -> None:
        self.condition_id = condition_id
        self.estimated_prob = estimated_prob
        self.confidence = confidence
        self.reasoning = reasoning
        self.sources = sources or []


def kelly_fraction(estimated_prob: Decimal, market_price: Decimal) -> Decimal:
    """Calculate Kelly Criterion optimal position size.

    f* = (bp - q) / b
    where b = odds offered (1/price - 1), p = estimated probability, q = 1 - p

    Returns a value between -1 and 1. Positive = buy YES, Negative = buy NO.
    """
    if market_price <= 0 or market_price >= 1:
        return Decimal("0")

    p = estimated_prob
    q = Decimal("1") - p
    b = (Decimal("1") / market_price) - Decimal("1")  # Decimal odds

    kelly = (b * p - q) / b

    # Half-Kelly for reduced variance (standard practice)
    return kelly / Decimal("2")


class AIAgentStrategy(Strategy):
    """Use LLM to estimate market probabilities from news and data.

    Pipeline:
    1. Fetch market question and context
    2. Gather relevant news/sources via web search
    3. LLM estimates probability with reasoning
    4. Compare estimated prob vs market price
    5. Size position using Kelly Criterion
    6. Execute when edge exceeds threshold
    """

    def __init__(self, settings: Settings) -> None:
        super().__init__(name="ai_agent", settings=settings)
        self._min_edge = Decimal("0.05")  # Minimum 5% edge to trade
        self._max_kelly_fraction = Decimal("0.25")  # Cap at 25% of bankroll
        self._bankroll = Decimal("1000")  # Starting bankroll for sizing
        self._estimates: dict[str, ProbabilityEstimate] = {}

    async def start(self) -> None:
        await super().start()
        if not self.settings.openai_api_key:
            logger.warning("ai_agent_no_openai_key", msg="Set OPENAI_API_KEY for LLM probability estimation")
        else:
            logger.info("ai_agent_started")

    async def on_data(self, data: MarketData) -> list[TradeSignal]:
        if self.state.value != "running":
            return []

        if not self.settings.openai_api_key:
            return []

        signals: list[TradeSignal] = []

        # Get or generate probability estimate
        estimate = self._estimates.get(data.condition_id)
        if not estimate:
            estimate = await self._estimate_probability(data)
            if estimate:
                self._estimates[data.condition_id] = estimate

        if not estimate:
            return []

        # Compare estimated prob to market price
        edge = estimate.estimated_prob - data.yes_price

        if abs(edge) < self._min_edge:
            return []

        # Kelly sizing
        kf = kelly_fraction(estimate.estimated_prob, data.yes_price)
        kf = max(-self._max_kelly_fraction, min(self._max_kelly_fraction, kf))

        if kf == 0:
            return []

        size = abs(kf) * self._bankroll
        side = "BUY_YES" if kf > 0 else "BUY_NO"
        price = data.yes_price if kf > 0 else data.no_price

        logger.info(
            "ai_signal",
            condition_id=data.condition_id,
            estimated_prob=str(estimate.estimated_prob),
            market_price=str(data.yes_price),
            edge=str(edge),
            kelly_fraction=str(kf),
            size=str(size),
        )

        signals.append(
            TradeSignal(
                condition_id=data.condition_id,
                side=side,
                price=price,
                size=size,
                reason=f"AI edge={edge:.3f}: est={estimate.estimated_prob:.2f} vs mkt={data.yes_price:.2f}. {estimate.reasoning[:100]}",
                confidence=float(estimate.confidence),
                strategy=self.name,
            )
        )

        return signals

    async def _estimate_probability(self, data: MarketData) -> ProbabilityEstimate | None:
        """Use LLM to estimate probability for a market question."""
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=self.settings.openai_api_key)
            response = await client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a prediction market analyst. Estimate the probability "
                            "that the following event will occur. Respond in JSON format: "
                            '{"probability": <float 0-1>, "confidence": <float 0-1>, '
                            '"reasoning": "<brief explanation>", "sources": ["<source1>"]}'
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Market question: {data.question}\nCurrent YES price: {data.yes_price}",
                    },
                ],
                temperature=0.3,
                max_tokens=300,
            )

            content = response.choices[0].message.content or ""
            # Parse JSON response
            import json

            result = json.loads(content)

            return ProbabilityEstimate(
                condition_id=data.condition_id,
                estimated_prob=Decimal(str(result.get("probability", 0.5))),
                confidence=Decimal(str(result.get("confidence", 0.3))),
                reasoning=result.get("reasoning", ""),
                sources=result.get("sources", []),
            )

        except Exception as e:
            logger.warning("ai_estimate_failed", condition_id=data.condition_id, error=str(e))
            return None

    async def on_fill(self, signal: TradeSignal, fill_price: Decimal, fill_size: Decimal) -> None:
        self._total_pnl += (signal.price - fill_price) * fill_size
        logger.info(
            "ai_fill",
            condition_id=signal.condition_id,
            side=signal.side,
            fill_price=str(fill_price),
            fill_size=str(fill_size),
        )
