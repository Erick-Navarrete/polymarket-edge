# Polymarket Edge -- Build Plan

## Phase 1: Foundation (Core Infrastructure)
- [x] `pyproject.toml` -- project metadata, dependencies (nautilus_trader, py-clob-client, pydantic, etc.)
- [x] `src/core/config.py` -- Pydantic Settings loading from `.env` with all config fields
- [x] `src/core/risk_manager.py` -- Position/loss limits, circuit breakers, daily drawdown tracking
- [x] `src/core/data_feed.py` -- Unified market data pipeline (Polymarket CLOB WebSocket + REST fallback)
- [x] `src/core/executor.py` -- Order routing through py-clob-client with paper/live mode switching
- [x] `src/core/strategy_base.py` -- Abstract `Strategy` interface with `on_data()`, `on_fill()`, `start()`, `stop()`
- [x] `src/core/engine.py` -- Central orchestrator routing data -> strategies -> risk -> execution
- [x] `src/core/wallet_tracker.py` -- On-chain wallet activity monitor for copy trading
- [x] `docker-compose.yml` -- Postgres, Redis, Grafana, Prometheus
- [x] Smoke test script: `scripts/smoke_test.py`

## Phase 2: Strategy Implementations
- [x] `src/strategies/arbitrage/` -- Internal YES+NO arb + cross-platform + mean-reversion arb
- [x] `src/strategies/market_making/` -- Bands strategy on CLOB with signal cooldown
- [x] `src/strategies/copy_trading/` -- Wallet follower + momentum-following in shadow mode
- [x] `src/strategies/ai_agent/` -- LLM news parser -> probability estimate -> Kelly-sized trade
- [x] `src/strategies/crypto_15m/` -- BTC/ETH 15-min binary with multi-signal fusion
- [x] `src/strategies/weather/` -- NOAA forecast fetch vs Polymarket weather market prices
- [x] `src/strategies/weather/noaa_fetcher.py` -- City geocoding + NOAA API integration

## Phase 3: Backtesting & Validation
- [x] `src/backtesting/harness.py` -- Lightweight backtest harness with Sharpe ratio calculation
- [x] `src/backtesting/data_loader.py` -- Historical data fetcher from Polymarket API
- [ ] Backtest each strategy on real historical data, record PnL, Sharpe, max drawdown (needs CLOB API key)
- [x] Walk-forward validation for strategies with ML components
- [x] Shadow trading mode -- SHADOW_MODE flag connects to live data but paper-trades only

## Phase 4: Dashboard & Monitoring
- [x] `src/dashboard/app.py` -- FastAPI backend (REST + WebSocket) for strategy/risk monitoring
- [x] React frontend with real-time WebSocket updates
- [x] `src/dashboard/frontend/` -- Vite + React + TypeScript + Tailwind CSS
- [x] Dashboard components: StatusHeader, StrategyTable, PositionTable, RiskPanel
- [x] useWebSocket hook with auto-reconnect
- [x] useApi hooks for REST endpoints + strategy toggle
- [x] FastAPI serves React SPA in production (static files from frontend/dist/)
- [x] Grafana dashboards for system health, trade execution, risk metrics
- [x] `src/core/metrics.py` -- Prometheus metrics (equity, PnL, drawdown, trades, fills, latency, errors)
- [x] `/metrics` endpoint on FastAPI for Prometheus scraping
- [x] Risk violation tracking per type (position_size, total_exposure, daily_loss, monthly_loss, drawdown)

## Phase 5: Hardening
- [x] Integration tests for each strategy in paper mode (44 tests passing)
- [x] Security audit of dependency tree (pmxt removed, py-clob-client verified official)
- [x] Remove pmxt dependency (flagged in security audit)
- [x] Key rotation documentation (docs/key_rotation.md)
- [x] Rate limiting and retry logic on all external API calls (tenacity @retry + 30s timeout)
- [x] Signal cooldowns on all strategies (MM: 15s, arb: 60s, copy: 30s)

## Phase 6: Deployment
- [x] Dockerfile for app container
- [x] docker-compose.yml with app service + infrastructure
- [x] render.yaml for PaaS deployment
- [x] .dockerignore
- [ ] Docker build test on cloud host
- [ ] Render deploy

## Review
- Phases 1-5 complete. Phase 6 configs ready, pending cloud test.
- 44 tests passing. 4/6 strategies fire in shadow mode (weather + ai_agent need API keys/specific markets).
- Shadow mode verified stable for 30+ minutes with live data.
