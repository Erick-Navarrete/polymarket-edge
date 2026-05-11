"""Historical data loader for backtesting.

Downloads and caches Polymarket market + trade data for offline backtesting.
Uses the Jon-Becker/prediction-market-analysis dataset format (Parquet).
"""

import asyncio
from decimal import Decimal
from pathlib import Path

import httpx
import structlog

from src.core.config import Settings
from src.core.strategy_base import MarketData

logger = structlog.get_logger()

DATA_DIR = Path("data")


class HistoricalDataLoader:
    """Load historical Polymarket data for backtesting."""

    def __init__(self, settings: Settings, data_dir: Path = DATA_DIR) -> None:
        self.settings = settings
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._http = httpx.AsyncClient(timeout=60)

    async def fetch_market_history(
        self,
        condition_id: str,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> list[dict]:
        """Fetch trade history for a specific market from Polymarket API."""
        params = {"market": condition_id, "limit": 500}
        if start_ts:
            params["start_ts"] = start_ts
        if end_ts:
            params["end_ts"] = end_ts

        try:
            resp = await self._http.get(
                f"{self.settings.gamma_api_url}/trades",
                params=params,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning("history_fetch_failed", condition_id=condition_id, error=str(e))
            return []

    async def fetch_price_history(
        self,
        token_id: str,
        interval: str = "1h",
        limit: int = 1000,
    ) -> list[dict]:
        """Fetch OHLCV price history for a token from the CLOB API."""
        try:
            resp = await self._http.get(
                f"{self.settings.clob_api_url}/prices",
                params={"token_id": token_id, "interval": interval, "limit": limit},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning("price_history_failed", token_id=token_id, error=str(e))
            return []

    def to_market_data(self, trades: list[dict]) -> list[MarketData]:
        """Convert raw trade data into MarketData objects for backtesting."""
        result = []
        for t in trades:
            try:
                result.append(MarketData(
                    condition_id=t.get("condition_id", t.get("market", "")),
                    question=t.get("question", ""),
                    yes_price=Decimal(str(t.get("yes_price", t.get("price", "0")))),
                    no_price=Decimal("1") - Decimal(str(t.get("yes_price", t.get("price", "0")))),
                    spread=Decimal(str(t.get("spread", "0.01"))),
                    volume_24h=Decimal(str(t.get("volume", "0"))),
                    timestamp=float(t.get("timestamp", t.get("created_at", 0))),
                    raw=t,
                ))
            except Exception:
                continue
        return sorted(result, key=lambda d: d.timestamp)

    async def close(self) -> None:
        await self._http.aclose()
