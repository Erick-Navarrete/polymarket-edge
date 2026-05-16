"""AI/LLM agent strategy — news parsing + probability estimation -> Kelly-sized trades.

Supports both OpenAI and Anthropic (Claude) APIs. When no API key is set,
falls back to a simple volume-price heuristic that estimates probability
from market activity patterns. This allows the strategy to participate in
shadow mode without an LLM.
"""

import time
import structlog
from decimal import Decimal
from typing import Any

from tenacity import retry, stop_after_attempt, wait_exponential

from src.core.config import Settings
from src.core.strategy_base import MarketData, Strategy, TradeSignal

logger = structlog.get_logger()

# Estimates expire after 30 minutes so LLM re-evaluates with fresh context
ESTIMATE_TTL_SECONDS = 1800


class ProbabilityEstimate:
    """LLM-generated probability estimate for a market."""

    def __init__(
        self,
        condition_id: str,
        estimated_prob: Decimal,
        confidence: Decimal,
        reasoning: str,
        sources: list[str] | None = None,
        created_at: float | None = None,
    ) -> None:
        self.condition_id = condition_id
        self.estimated_prob = estimated_prob
        self.confidence = confidence
        self.reasoning = reasoning
        self.sources = sources or []
        self.created_at = created_at or time.monotonic()

    @property
    def is_stale(self) -> bool:
        return (time.monotonic() - self.created_at) > ESTIMATE_TTL_SECONDS


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
    b = (Decimal("1") / market_price) - Decimal("1") # Decimal odds

    kelly = (b * p - q) / b

    # Half-Kelly for reduced variance (standard practice)
    return kelly / Decimal("2")


class AIAgentStrategy(Strategy):
    """Use LLM to estimate market probabilities from news and data.

    Pipeline:
    1. Fetch market question and context
    2. LLM estimates probability with reasoning
    3. Compare estimated prob vs market price
    4. Size position using Kelly Criterion
    5. Execute when edge exceeds threshold

    When OPENAI_API_KEY is not set, uses a heuristic fallback:
    - Markets near 0.50 are hard to call -> skip
    - Extreme prices (very high or very low) tend to be well-informed -> skip
    - Moderate deviations from 0.50 with high volume suggest informed flow
      -> estimate prob based on volume-adjusted mean reversion
    """

    def __init__(self, settings: Settings) -> None:
        super().__init__(name="ai_agent", settings=settings)
        self._min_edge = Decimal("0.05") # Minimum 5% edge to trade
        self._max_kelly_fraction = Decimal("0.25") # Cap at 25% of bankroll
        self._bankroll = Decimal("1000") # Starting bankroll for sizing
        self._estimates: dict[str, ProbabilityEstimate] = {}
        self._last_signal_time: dict[str, float] = {}
        self._signal_cooldown: float = 120.0 # 2 min between signals per market
        # Heuristic state (used when no LLM key)
        self._price_history: dict[str, list[Decimal]] = {}
        # Reusable clients (created lazily)
        self._openai_client: Any | None = None
        self._anthropic_client: Any | None = None
        self._llm_provider: str | None = None

    async def start(self) -> None:
        await super().start()
        self._llm_provider = self._detect_llm_provider()
        if self._llm_provider:
            logger.info("ai_agent_started", provider=self._llm_provider)
        else:
            logger.info("ai_agent_heuristic_mode", msg="No OPENAI_API_KEY or ANTHROPIC_API_KEY — using volume-price heuristic fallback")

    def _detect_llm_provider(self) -> str | None:
        """Detect which LLM provider is available."""
        if self.settings.openai_api_key:
            return "openai"
        if getattr(self.settings, 'anthropic_api_key', ''):
            return "anthropic"
        return None

    async def stop(self) -> None:
        await super().stop()
        if self._openai_client is not None:
            await self._openai_client.close()
            self._openai_client = None
        if self._anthropic_client is not None:
            await self._anthropic_client.close()
            self._anthropic_client = None

    async def on_data(self, data: MarketData) -> list[TradeSignal]:
        if self.state.value != "running":
            return []

        # Cooldown check
        now = time.monotonic()
        last = self._last_signal_time.get(data.condition_id, 0)
        if now - last < self._signal_cooldown:
            return []

        signals: list[TradeSignal] = []

        if self._llm_provider:
            # LLM-based estimation (with TTL-based re-evaluation)
            estimate = self._estimates.get(data.condition_id)
            if estimate and estimate.is_stale:
                del self._estimates[data.condition_id]
                estimate = None
            if not estimate:
                estimate = await self._estimate_probability(data)
                if estimate:
                    self._estimates[data.condition_id] = estimate
            if estimate:
                signals = self._make_signal(data, estimate)
        else:
            # Heuristic fallback
            signals = self._heuristic_signal(data)

        if signals:
            self._last_signal_time[data.condition_id] = now

        return signals

    def _make_signal(self, data: MarketData, estimate: ProbabilityEstimate) -> list[TradeSignal]:
        """Generate a trade signal from a probability estimate."""
        edge = estimate.estimated_prob - data.yes_price

        if abs(edge) < self._min_edge:
            return []

        kf = kelly_fraction(estimate.estimated_prob, data.yes_price)
        kf = max(-self._max_kelly_fraction, min(self._max_kelly_fraction, kf))

        if kf == 0:
            return []

        size = abs(kf) * self._bankroll
        side = "BUY_YES" if kf > 0 else "BUY_NO"
        price = data.yes_price if kf > 0 else data.no_price

        source = "llm" if self._llm_provider else "heuristic"
        logger.info(
            "ai_signal",
            condition_id=data.condition_id[:16],
            estimated_prob=str(estimate.estimated_prob),
            market_price=str(data.yes_price),
            edge=str(edge),
            kelly_fraction=str(kf),
            size=str(size),
            source=source,
        )

        return [TradeSignal(
            condition_id=data.condition_id,
            side=side,
            price=price,
            size=size,
            reason=f"AI edge={edge:.3f}: est={estimate.estimated_prob:.2f} vs mkt={data.yes_price:.2f}. {estimate.reasoning[:100]}",
            confidence=float(estimate.confidence),
            strategy=self.name,
        )]

    def _heuristic_signal(self, data: MarketData) -> list[TradeSignal]:
        """Volume-price heuristic for shadow mode without LLM.

        Logic: moderate prices (0.20-0.80) with high volume suggest the
        market is well-calibrated. We look for small mispricings by
        estimating that the "fair" price is slightly mean-reverting
        from the current price toward 0.50, scaled by volume.

        High-volume markets get more weight (more informed).
        Low-volume markets are too noisy to estimate.
        """
        cid = data.condition_id

        # Track price history
        if cid not in self._price_history:
            self._price_history[cid] = []
        self._price_history[cid].append(data.yes_price)
        self._price_history[cid] = self._price_history[cid][-20:] # Keep last 20

        history = self._price_history[cid]
        if len(history) < 5:
            return []

        # Skip extreme prices — these are likely well-informed
        if data.yes_price < Decimal("0.10") or data.yes_price > Decimal("0.90"):
            return []

        # Skip very stale low-volume markets
        if data.volume_24h < Decimal("1000"):
            return []

        # Estimate "fair value" as a blend of current price and recent average
        recent_avg = sum(history[-10:]) / Decimal(str(len(history[-10:])))

        # If current price moved away from recent average, estimate reversion
        deviation = data.yes_price - recent_avg
        if abs(deviation) < Decimal("0.03"):
            return [] # No meaningful edge

        # Estimate probability: blend between current price and mean-reversion
        reversion_strength = Decimal("0.3") # How much we weight mean reversion
        estimated_prob = data.yes_price - deviation * reversion_strength

        # Only trade if the edge is significant
        edge = estimated_prob - data.yes_price
        if abs(edge) < self._min_edge:
            return []

        # Scale confidence by volume (higher volume = more reliable signal)
        volume_confidence = min(data.volume_24h / Decimal("100000"), Decimal("1"))
        confidence = volume_confidence * Decimal("0.4") # Low confidence for heuristic

        kf = kelly_fraction(estimated_prob, data.yes_price)
        kf = max(-self._max_kelly_fraction, min(self._max_kelly_fraction, kf))

        if kf == 0:
            return []

        size = abs(kf) * self._bankroll
        side = "BUY_YES" if kf > 0 else "BUY_NO"
        price = data.yes_price if kf > 0 else data.no_price

        logger.info(
            "ai_heuristic_signal",
            condition_id=cid[:16],
            edge=str(edge),
            estimated_prob=str(estimated_prob),
            market_price=str(data.yes_price),
        )

        return [TradeSignal(
            condition_id=cid,
            side=side,
            price=price,
            size=size,
            reason=f"Heuristic edge={edge:.3f}: est={estimated_prob:.2f} vs mkt={data.yes_price:.2f} (reversion)",
            confidence=float(confidence),
            strategy=self.name,
        )]

    async def _estimate_probability(self, data: MarketData) -> ProbabilityEstimate | None:
        """Use LLM to estimate probability for a market question."""
        try:
            response = await self._call_llm(data)

            content = response.choices[0].message.content or ""
            import json

            result = json.loads(content)

            return ProbabilityEstimate(
                condition_id=data.condition_id,
                estimated_prob=Decimal(str(result.get("probability", 0.5))),
                confidence=Decimal(str(result.get("confidence", 0.3))),
                reasoning=result.get("reasoning", ""),
                sources=result.get("sources", []),
                created_at=time.monotonic(),
            )

        except Exception as e:
            logger.warning("ai_estimate_failed", condition_id=data.condition_id[:16], error=str(e))
            return None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def _call_llm(self, data: MarketData) -> Any:
        """Call LLM API with retry for transient failures only."""
        if self._llm_provider == "openai":
            return await self._call_openai(data)
        elif self._llm_provider == "anthropic":
            return await self._call_anthropic(data)
        raise RuntimeError(f"Unknown LLM provider: {self._llm_provider}")

    SYSTEM_PROMPT = (
        "You are a prediction market analyst. Estimate the probability "
        "that the following event will occur. Respond in JSON format: "
        '{"probability": <float 0-1>, "confidence": <float 0-1>, '
        '"reasoning": "<brief explanation>", "sources": ["<source1>"]}'
    )

    async def _call_openai(self, data: MarketData) -> Any:
        """Call OpenAI API for probability estimation."""
        if self._openai_client is None:
            from openai import AsyncOpenAI
            self._openai_client = AsyncOpenAI(api_key=self.settings.openai_api_key, timeout=30)

        return await self._openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": f"Market question: {data.question}\nCurrent YES price: {data.yes_price}"},
            ],
            temperature=0.3,
            max_tokens=300,
        )

    async def _call_anthropic(self, data: MarketData) -> Any:
        """Call Anthropic (Claude) API for probability estimation."""
        import json as _json

        api_key = getattr(self.settings, 'anthropic_api_key', '')
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")

        if self._anthropic_client is None:
            try:
                from anthropic import AsyncAnthropic
                self._anthropic_client = AsyncAnthropic(api_key=api_key, timeout=30)
            except ImportError:
                # Fallback to raw HTTP if SDK not available
                self._anthropic_client = None

        if self._anthropic_client:
            response = await self._anthropic_client.messages.create(
                model="claude-sonnet-4-6-20250514",
                max_tokens=300,
                system=self.SYSTEM_PROMPT,
                messages=[
                    {"role": "user", "content": f"Market question: {data.question}\nCurrent YES price: {data.yes_price}"},
                ],
                temperature=0.3,
            )
            # Wrap in a compatible format for _estimate_probability
            return _AnthropicResponse(response)
        else:
            # Raw HTTP fallback
            import httpx
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-sonnet-4-6-20250514",
                        "max_tokens": 300,
                        "system": self.SYSTEM_PROMPT,
                        "messages": [
                            {"role": "user", "content": f"Market question: {data.question}\nCurrent YES price: {data.yes_price}"},
                        ],
                        "temperature": 0.3,
                    },
                )
                resp.raise_for_status()
                return _AnthropicHTTPResponse(resp.json())

    async def on_fill(self, signal: TradeSignal, fill_price: Decimal, fill_size: Decimal) -> None:
        self._total_pnl += (signal.price - fill_price) * fill_size
        logger.info(
            "ai_fill",
            condition_id=signal.condition_id[:16],
            side=signal.side,
            fill_price=str(fill_price),
            fill_size=str(fill_size),
        )


class _AnthropicResponse:
    """Wrapper to make Anthropic SDK response compatible with OpenAI format."""

    def __init__(self, response: Any) -> None:
        content = response.content[0].text if response.content else "{}"
        self.choices = [_Choice(_Message(content=content))]


class _AnthropicHTTPResponse:
    """Wrapper for raw HTTP Anthropic response."""

    def __init__(self, data: dict) -> None:
        blocks = data.get("content", [])
        text = blocks[0].get("text", "{}") if blocks else "{}"
        self.choices = [_Choice(_Message(content=text))]


class _Choice:
    def __init__(self, message: "_Message") -> None:
        self.message = message


class _Message:
    def __init__(self, content: str) -> None:
        self.content = content
