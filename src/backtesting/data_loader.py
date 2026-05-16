"""Historical data loader for backtesting.

Downloads and caches Polymarket market + trade data for offline backtesting.
Uses CLOB authenticated API when credentials are available for OHLCV data,
otherwise falls back to Gamma API trade history with pseudo-timeseries construction.
"""

from decimal import Decimal
from pathlib import Path

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

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
        self._http = httpx.AsyncClient(timeout=30)
        self._clob_client = None

    async def _ensure_clob_client(self):
        """Lazily initialize py-clob-client for authenticated CLOB requests.

        Three auth levels:
        1. Full L2 credentials (key+secret+passphrase) — best, all endpoints available
        2. L1 wallet key — can derive L2 credentials via create_or_derive_api_creds
        3. No auth — falls back to Gamma public endpoints for pseudo-timeseries
        """
        if self._clob_client is not None:
            return self._clob_client is not False

        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds
        except ImportError:
            logger.warning("data_loader_no_py_clob_client", msg="Install py-clob-client for authenticated OHLCV")
            self._clob_client = False
            return False

        # Try full L2 credentials first
        if all([
            self.settings.polymarket_api_key,
            self.settings.polymarket_api_secret,
            self.settings.polymarket_api_passphrase,
        ]):
            try:
                creds = ApiCreds(
                    api_key=self.settings.polymarket_api_key,
                    api_secret=self.settings.polymarket_api_secret,
                    api_passphrase=self.settings.polymarket_api_passphrase,
                )
                self._clob_client = ClobClient(
                    self.settings.clob_api_url,
                    chain_id=137,
                    creds=creds,
                    timeout=30,
                )
                logger.info("data_loader_clob_l2_authenticated")
                return True
            except Exception as e:
                logger.warning("data_loader_clob_l2_init_failed", error=str(e))

        # Try L1 wallet key to derive L2 credentials
        if self.settings.polymarket_api_key and self.settings.polygon_wallet_private_key:
            try:
                self._clob_client = ClobClient(
                    self.settings.clob_api_url,
                    chain_id=137,
                    key=self.settings.polygon_wallet_private_key,
                    timeout=30,
                )
                derived_creds = self._clob_client.create_or_derive_api_creds()
                self._clob_client.set_api_creds(derived_creds)
                logger.info("data_loader_clob_l1_derived", api_key=derived_creds.api_key[:12])
                return True
            except Exception as e:
                logger.warning("data_loader_clob_l1_derive_failed", error=str(e))

        # L2 key only — limited access, try public-ish endpoints
        if self.settings.polymarket_api_key:
            logger.info("data_loader_clob_partial_auth", msg="L2 key present but no secret/passphrase — limited to public endpoints + Gamma fallback")
            self._clob_client = False
            return False

        logger.info("data_loader_no_clob_auth", msg="No CLOB API credentials — falling back to Gamma public endpoints")
        self._clob_client = False
        return False

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def fetch_market_history(
        self,
        condition_id: str,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> list[dict]:
        """Fetch trade history for a specific market from Gamma API (public)."""
        params = {"market": condition_id, "limit": 500}
        if start_ts:
            params["start_ts"] = start_ts
        if end_ts:
            params["end_ts"] = end_ts

        resp = await self._http.get(
            f"{self.settings.gamma_api_url}/trades",
            params=params,
        )
        resp.raise_for_status()
        return resp.json()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def fetch_price_history(
        self,
        token_id: str,
        interval: str = "1h",
        limit: int = 1000,
    ) -> list[dict]:
        """Fetch OHLCV price history for a token.

        Uses authenticated CLOB API when credentials are available.
        Falls back to Gamma trade history + pseudo-timeseries otherwise.
        """
        has_auth = await self._ensure_clob_client()

        if has_auth and self._clob_client:
            try:
                result = self._clob_client.get_prices_history(
                    token_id=token_id,
                    interval=interval,
                    limit=limit,
                )
                if result:
                    logger.info("data_loader_ohlcv_fetched", token_id=token_id[:16], candles=len(result))
                    return result
            except Exception as e:
                logger.warning("data_loader_clob_prices_failed", error=str(e), msg="Falling back to Gamma trades")

        # Fallback: build pseudo-timeseries from Gamma trade history
        # Also try CLOB trades endpoint (may work without full auth for some markets)
        pseudo = await self._fetch_pseudo_timeseries(token_id, interval, limit)
        if pseudo:
            return pseudo

        # Last resort: try CLOB market trades by condition_id
        return await self._fetch_clob_trades(token_id, interval, limit)

    async def _fetch_pseudo_timeseries(
        self,
        token_id: str,
        interval: str = "1h",
        limit: int = 1000,
    ) -> list[dict]:
        """Build pseudo-OHLCV from Gamma trade history.

        The Gamma /trades endpoint is public and returns individual trades.
        We aggregate them into time buckets matching the requested interval.
        """
        try:
            # Find the market/condition_id for this token via Gamma /markets
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    f"{self.settings.gamma_api_url}/markets",
                    params={"limit": 1, "clob_token_ids": token_id},
                )
                if resp.status_code == 200:
                    markets = resp.json()
                    condition_id = markets[0].get("condition_id", "") if markets else ""
                else:
                    condition_id = ""

            if not condition_id:
                logger.warning("data_loader_no_condition_id", token_id=token_id[:16])
                return []

            trades_raw = await self.fetch_market_history(condition_id)
            if not trades_raw:
                return []

            # Aggregate trades into interval buckets
            interval_sec = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}.get(interval, 3600)
            buckets: dict[int, list[Decimal]] = {}

            for t in trades_raw:
                ts = int(float(t.get("timestamp", t.get("created_at", 0))))
                bucket_ts = (ts // interval_sec) * interval_sec
                price = Decimal(str(t.get("price", t.get("yes_price", "0"))))
                if price > 0:
                    buckets.setdefault(bucket_ts, []).append(price)

            result = []
            for bts in sorted(buckets.keys())[:limit]:
                prices = buckets[bts]
                result.append({
                    "t": bts,
                    "o": str(prices[0]),
                    "h": str(max(prices)),
                    "l": str(min(prices)),
                    "c": str(prices[-1]),
                    "v": str(len(prices)),
                    "source": "gamma_pseudo_ohlcv",
                })

            logger.info("data_loader_pseudo_timeseries", token_id=token_id[:16], buckets=len(result))
            return result

        except Exception as e:
            logger.warning("data_loader_pseudo_timeseries_failed", error=str(e))
            return []

    def to_market_data(self, trades: list[dict]) -> list[MarketData]:
        """Convert raw trade data into MarketData objects for backtesting."""
        result = []
        for t in trades:
            try:
                price = Decimal(str(t.get("yes_price", t.get("price", "0"))))
                if price <= 0:
                    continue
                result.append(MarketData(
                    condition_id=t.get("condition_id", t.get("market", "")),
                    question=t.get("question", ""),
                    yes_price=price,
                    no_price=Decimal("1") - price,
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

    async def _fetch_clob_trades(
        self,
        token_id: str,
        interval: str = "1h",
        limit: int = 1000,
    ) -> list[dict]:
        """Try fetching trades from CLOB /markets endpoint by condition_id.

        This uses public CLOB endpoints that don't require authentication.
        """
        try:
            # Look up condition_id from markets data
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    f"{self.settings.clob_api_url}/markets",
                    params={"next_cursor": "MA=="},
                )
                if resp.status_code != 200:
                    return []

                markets_data = resp.json().get("data", [])
                condition_id = ""
                for m in markets_data:
                    for token in m.get("tokens", []):
                        if token.get("token_id") == token_id:
                            condition_id = m.get("condition_id", "")
                            break
                    if condition_id:
                        break

                if not condition_id:
                    return []

                # Try CLOB trades endpoint
                resp2 = await client.get(
                    f"{self.settings.clob_api_url}/market-trades-events",
                    params={"condition_id": condition_id},
                )
                if resp2.status_code != 200:
                    return []

                trades = resp2.json().get("trades", resp2.json()) if isinstance(resp2.json(), dict) else resp2.json()
                if not trades:
                    return []

                # Aggregate into OHLCV buckets
                interval_sec = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}.get(interval, 3600)
                buckets: dict[int, list[Decimal]] = {}

                for t in trades:
                    ts = int(float(t.get("timestamp", 0)))
                    bucket_ts = (ts // interval_sec) * interval_sec
                    price = Decimal(str(t.get("price", "0")))
                    if price > 0:
                        buckets.setdefault(bucket_ts, []).append(price)

                result = []
                for bts in sorted(buckets.keys())[:limit]:
                    prices = buckets[bts]
                    result.append({
                        "t": bts,
                        "o": str(prices[0]),
                        "h": str(max(prices)),
                        "l": str(min(prices)),
                        "c": str(prices[-1]),
                        "v": str(len(prices)),
                        "source": "clob_public_trades",
                    })

                logger.info("data_loader_clob_trades", token_id=token_id[:16], buckets=len(result))
                return result

        except Exception as e:
            logger.warning("data_loader_clob_trades_failed", error=str(e))
            return []
