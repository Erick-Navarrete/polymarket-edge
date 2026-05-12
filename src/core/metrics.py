"""Prometheus metrics for Polymarket Edge trading system."""

from prometheus_client import Counter, Gauge, Histogram

# --- Equity & PnL ---
equity_total = Gauge("polymarket_equity_total", "Current total equity in USD")
daily_pnl = Gauge("polymarket_daily_pnl", "Daily realized PnL in USD")
drawdown_pct = Gauge("polymarket_drawdown_pct", "Current drawdown percentage")
total_exposure = Gauge("polymarket_total_exposure", "Total portfolio exposure in USD")

# --- Risk ---
halted = Gauge("polymarket_halted", "Whether the system is halted (0=running, 1=halted)")
risk_violations = Counter(
    "polymarket_risk_violations_total",
    "Total risk violations by type",
    ["violation_type"],
)

# --- Strategy ---
strategy_pnl = Gauge(
    "polymarket_strategy_pnl",
    "Current PnL by strategy",
    ["strategy"],
)
strategy_state = Gauge(
    "polymarket_strategy_state",
    "Strategy state (0=stopped, 1=starting, 2=running, 3=paused, 4=error)",
    ["strategy"],
)

# --- Execution ---
trades_total = Counter(
    "polymarket_trades_total",
    "Total trades executed",
    ["strategy", "side"],
)
fills_total = Counter(
    "polymarket_fills_total",
    "Total fills received",
    ["strategy", "side"],
)
errors_total = Counter(
    "polymarket_errors_total",
    "Total errors by strategy and type",
    ["strategy", "error_type"],
)
execution_latency = Histogram(
    "polymarket_execution_latency_seconds",
    "Order execution latency in seconds",
    ["strategy"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

# --- API ---
api_latency = Histogram(
    "polymarket_api_latency_seconds",
    "External API request latency in seconds",
    ["endpoint"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
)

# --- Data Feed ---
feed_up = Gauge("polymarket_feed_up", "Whether the data feed is connected (0/1)")
ws_connections = Gauge("polymarket_ws_connections", "Active WebSocket connections")
