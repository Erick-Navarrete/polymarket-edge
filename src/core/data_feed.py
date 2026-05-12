import asyncio
import json
from decimal import Decimal
from typing import AsyncIterator

import httpx
import structlog
import websockets
from tenacity import retry, stop_after_attempt, wait_exponential

from src.core.config import Settings
from src.core.strategy_base import MarketData

logger = structlog.get_logger()

WS_RECONNECT_DELAY = 3  # seconds between reconnect attempts


class DataFeed:
    """Unified market data pipeline from Polymarket CLOB + Gamma APIs."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._http_client = httpx.AsyncClient(
            base_url=settings.clob_api_url,
            timeout=settings.order_timeout_seconds,
        )
        self._ws_url = settings.clob_ws_url

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def get_markets(self, limit: int = 100, offset: int = 0) -> list[dict]:
        """Fetch available markets from Gamma API."""
        async with httpx.AsyncClient(
            base_url=self.settings.gamma_api_url,
            timeout=self.settings.order_timeout_seconds,
        ) as client:
            resp = await client.get(
                "/markets",
                params={"limit": limit, "offset": offset, "closed": False},
            )
            resp.raise_for_status()
            return resp.json()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def get_market(self, condition_id: str) -> dict:
        """Fetch a single market by condition ID."""
        resp = await self._http_client.get(f"/markets/{condition_id}")
        resp.raise_for_status()
        return resp.json()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def get_orderbook(self, token_id: str) -> dict:
        """Fetch current orderbook for a token."""
        resp = await self._http_client.get("/book", params={"token_id": token_id})
        resp.raise_for_status()
        return resp.json()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def get_midpoint(self, token_id: str) -> Decimal:
        """Get current midpoint price for a token."""
        resp = await self._http_client.get("/midpoint", params={"token_id": token_id})
        resp.raise_for_status()
        data = resp.json()
        return Decimal(str(data.get("mid", "0")))

    async def stream_market(self, token_ids: list[str]) -> AsyncIterator[MarketData]:
        """Stream real-time market data via WebSocket with auto-reconnect."""
        subscribe_msg = {
            "auth": {},
            "markets": [
                {"market": {"condition_id": tid}, "side": "all"}
                for tid in token_ids
            ],
        }

        while True:
            try:
                async with websockets.connect(
                self._ws_url,
                ping_timeout=30,
                close_timeout=10,
            ) as ws:
                    await ws.send(json.dumps({"type": "subscribe", **subscribe_msg}))
                    logger.info("ws_subscribed", tokens=len(token_ids))

                    async for raw_msg in ws:
                        msg = json.loads(raw_msg)
                        if msg.get("type") == "price_change":
                            data = self._parse_price_change(msg)
                            if data:
                                yield data

            except (
                websockets.ConnectionClosed,
                websockets.InvalidStatusCode,
                OSError,
            ) as e:
                logger.warning(
                    "ws_disconnected",
                    error=str(e),
                    reconnect_in=WS_RECONNECT_DELAY,
                )
                await asyncio.sleep(WS_RECONNECT_DELAY)
                logger.info("ws_reconnecting")

    def _parse_price_change(self, msg: dict) -> MarketData | None:
        """Convert a WebSocket price_change message to MarketData."""
        try:
            market_data = msg.get("market_data", {})
            return MarketData(
                condition_id=market_data.get("condition_id", ""),
                question=market_data.get("question", ""),
                yes_price=Decimal(str(market_data.get("yes_price", "0"))),
                no_price=Decimal(str(market_data.get("no_price", "0"))),
                spread=Decimal(str(market_data.get("spread", "0"))),
                volume_24h=Decimal(str(market_data.get("volume_24h", "0"))),
                timestamp=market_data.get("timestamp", 0),
                raw=msg,
            )
        except Exception:
            logger.warning("ws_parse_error", raw=msg)
            return None

    async def close(self) -> None:
        """Clean up HTTP client."""
        await self._http_client.aclose()
