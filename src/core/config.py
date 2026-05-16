from decimal import Decimal
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # === Trading Mode ===
    live_mode: bool = False

    # Shadow mode: connect to live market data feeds but only paper-trade.
    # Different from plain paper mode (which can use simulated/cached data).
    # Use this to validate strategies against real-time prices before going live.
    shadow_mode: bool = False

    # === Wallet ===
    polygon_wallet_address: str = ""
    polygon_wallet_private_key: str = ""

    # === Polymarket API ===
    polymarket_api_key: str = ""
    polymarket_api_secret: str = ""
    polymarket_api_passphrase: str = ""

    # === Kalshi API ===
    kalshi_api_key: str = ""
    kalshi_api_secret: str = ""

    # === Data Sources ===
    binance_api_key: str = ""
    binance_api_secret: str = ""
    coinbase_api_key: str = ""
    coinbase_api_secret: str = ""

    # === AI / LLM ===
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    # === Database ===
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "polymarket_edge"
    postgres_user: str = "polymarket"
    postgres_password: str = "changeme"

    @property
    def postgres_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # === Redis ===
    redis_host: str = "localhost"
    redis_port: int = 6379

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}"

    # === Risk Limits ===
    max_daily_loss_pct: Decimal = Decimal("5")
    max_monthly_loss_pct: Decimal = Decimal("15")
    max_drawdown_pct: Decimal = Decimal("25")
    max_position_size_usd: Decimal = Decimal("500")
    max_total_exposure_usd: Decimal = Decimal("5000")

    # === Monitoring ===
    grafana_admin_password: str = "admin"
    slack_webhook_url: str = ""

    # === Data Feed ===
    clob_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    clob_api_url: str = "https://clob.polymarket.com"
    gamma_api_url: str = "https://gamma-api.polymarket.com"

    # === Execution ===
    order_timeout_seconds: int = 30
    max_retries: int = 3
    retry_base_delay_seconds: float = 1.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
