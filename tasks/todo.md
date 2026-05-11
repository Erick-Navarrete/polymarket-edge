# Polymarket Edge — Build Plan

## Phase 1: Foundation (Core Infrastructure)
- [x] `pyproject.toml` — project metadata, dependencies (nautilus_trader, py-clob-client, pmxt, pydantic, etc.)
- [x] `src/core/config.py` — Pydantic Settings loading from `.env` with all config fields
- [x] `src/core/risk_manager.py` — Position/loss limits, circuit breakers, daily drawdown tracking
- [x] `src/core/data_feed.py` — Unified market data pipeline (Polymarket CLOB WebSocket + REST fallback)
- [x] `src/core/executor.py` — Order routing through py-clob-client with paper/live mode switching
- [x] `src/core/strategy_base.py` — Abstract `Strategy` interface with `on_data()`, `on_fill()`, `start()`, `stop()`
- [x] `docker-compose.yml` — Postgres, Redis, Grafana, Prometheus
- [ ] Smoke test: spin up docker, connect to Polymarket API in read-only mode, stream one market

## Phase 2: Strategy Implementations
- [x] `src/strategies/arbitrage/` — Internal YES+NO arb + Polymarket/Kalshi cross-platform arb
- [x] `src/strategies/market_making/` — Bands strategy on CLOB (reference: poly-market-maker)
- [x] `src/strategies/copy_trading/` — On-chain wallet tracker with configurable copy ratio
- [x] `src/strategies/ai_agent/` — LLM news parser -> probability estimate -> Kelly-sized trade
- [x] `src/strategies/crypto_15m/` — BTC/ETH 15-min binary with multi-signal fusion
- [x] `src/strategies/weather/` — NOAA forecast fetch vs Polymarket weather market prices

## Phase 3: Backtesting & Validation
- [x] `src/backtesting/` — NautilusTrader backtest harness, historical data loader
- [ ] Backtest each strategy on historical data, record PnL, Sharpe, max drawdown
- [ ] Walk-forward validation for strategies with ML components
- [ ] Shadow trading mode — run strategies in paper alongside live market data before committing capital

## Phase 4: Dashboard & Monitoring
- [ ] `src/dashboard/` — FastAPI backend serving strategy state, positions, PnL
- [ ] React frontend with real-time WebSocket updates
- [ ] Grafana dashboards for system health, trade execution, risk metrics

## Phase 5: Hardening
- [ ] Integration tests for each strategy in paper mode
- [ ] Security audit of dependency tree (check for typosquatted packages)
- [ ] Rate limiting and retry logic on all external API calls
- [ ] Key rotation documentation

## Review
- Phase 1 & 2 scaffolded. All 6 strategies implemented with core interfaces.
- Backtesting harness created (lightweight + NautilusTrader path documented).
- Tests written for risk manager, arbitrage, market making, and Kelly Criterion.
- Smoke test and backtest-on-real-data remain as next steps before committing capital.
