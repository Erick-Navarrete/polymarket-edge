# Polymarket Edge — Build Plan

## Phase 1: Foundation (Core Infrastructure)
- [x] `pyproject.toml` — project metadata, dependencies (nautilus_trader, py-clob-client, pmxt, pydantic, etc.)
- [x] `src/core/config.py` — Pydantic Settings loading from `.env` with all config fields
- [x] `src/core/risk_manager.py` — Position/loss limits, circuit breakers, daily drawdown tracking
- [x] `src/core/data_feed.py` — Unified market data pipeline (Polymarket CLOB WebSocket + REST fallback)
- [x] `src/core/executor.py` — Order routing through py-clob-client with paper/live mode switching
- [x] `src/core/strategy_base.py` — Abstract `Strategy` interface with `on_data()`, `on_fill()`, `start()`, `stop()`
- [x] `src/core/engine.py` — Central orchestrator routing data -> strategies -> risk -> execution
- [x] `src/core/wallet_tracker.py` — On-chain wallet activity monitor for copy trading
- [x] `docker-compose.yml` — Postgres, Redis, Grafana, Prometheus
- [x] Smoke test script: `scripts/smoke_test.py`

## Phase 2: Strategy Implementations
- [x] `src/strategies/arbitrage/` — Internal YES+NO arb + Polymarket/Kalshi cross-platform arb
- [x] `src/strategies/market_making/` — Bands strategy on CLOB (reference: poly-market-maker)
- [x] `src/strategies/copy_trading/` — On-chain wallet tracker with configurable copy ratio
- [x] `src/strategies/ai_agent/` — LLM news parser -> probability estimate -> Kelly-sized trade
- [x] `src/strategies/crypto_15m/` — BTC/ETH 15-min binary with multi-signal fusion
- [x] `src/strategies/weather/` — NOAA forecast fetch vs Polymarket weather market prices
- [x] `src/strategies/weather/noaa_fetcher.py` — City geocoding + NOAA API integration

## Phase 3: Backtesting & Validation
- [x] `src/backtesting/harness.py` — Lightweight backtest harness with Sharpe ratio calculation
- [x] `src/backtesting/data_loader.py` — Historical data fetcher from Polymarket API
- [ ] Backtest each strategy on historical data, record PnL, Sharpe, max drawdown
- [ ] Walk-forward validation for strategies with ML components
- [ ] Shadow trading mode — run strategies in paper alongside live market data

## Phase 4: Dashboard & Monitoring
- [x] `src/dashboard/app.py` — FastAPI backend (REST + WebSocket) for strategy/risk monitoring
- [x] React frontend with real-time WebSocket updates
- [x] `src/dashboard/frontend/` — Vite + React + TypeScript + Tailwind CSS
- [x] Dashboard components: StatusHeader, StrategyTable, PositionTable, RiskPanel
- [x] useWebSocket hook with auto-reconnect
- [x] useApi hooks for REST endpoints + strategy toggle
- [x] FastAPI serves React SPA in production (static files from frontend/dist/)
- [ ] Grafana dashboards for system health, trade execution, risk metrics

## Phase 5: Hardening
- [ ] Integration tests for each strategy in paper mode
- [ ] Security audit of dependency tree (check for typosquatted packages)
- [ ] Rate limiting and retry logic on all external API calls
- [ ] Key rotation documentation

## Review
- Phase 1 complete. Phase 2 complete (6 strategies). Phase 3 core done (harness + data loader).
- Dashboard backend complete. React frontend + Grafana dashboards remain.
- Next: Grafana dashboards, backtest on real data, then hardening.
