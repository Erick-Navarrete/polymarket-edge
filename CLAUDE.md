# Polymarket Edge — Predictive Trading Platform

## Project Overview

A modular Polymarket predictive trading platform that combines multiple strategies (arbitrage, market making, copy trading, AI/LLM-driven signals) into a single unified system with backtesting, shadow trading, and live execution capabilities.

## Architecture Philosophy

- **Modular strategy system**: Each strategy is an isolated plugin that can run independently or composed together
- **Paper-first**: All strategies default to dry-run/paper mode. Live mode requires explicit `--live` flag
- **No keys in code**: Private keys, API keys, and secrets live exclusively in `.env` (git-ignored)
- **Unified data layer**: A shared market data pipeline feeds all strategies from the same WebSocket/API sources
- **Risk gates**: Every strategy passes through a shared risk manager (daily loss caps, position limits, circuit breakers)

## Tech Stack

- **Language**: Python 3.11+ (primary), TypeScript for dashboard/frontend
- **Trading framework**: NautilusTrader (production-grade backtest + live with zero strategy rewrite)
- **Data sources**: Polymarket CLOB API, Gamma API, Binance/Coinbase WebSocket feeds, news/sentiment APIs
- **Storage**: PostgreSQL (trades, positions, market data), Redis (caching, real-time state, mode switching)
- **Infrastructure**: Docker Compose for all services, Grafana + Prometheus for monitoring
- **AI/LLM**: OpenAI API for news-based probability estimation, ChromaDB for RAG

## Planned Strategy Modules

1. **Arbitrage** — Cross-platform (Polymarket <-> Kalshi) and internal (YES+NO < $1) arbitrage
2. **Market Making** — Bands strategy on the Polymarket CLOB, earning the spread
3. **Copy Trading** — On-chain wallet tracking of profitable traders, configurable copy ratio
4. **AI/LLM Agent** — News parsing + probability estimation -> position sizing via Kelly Criterion
5. **Crypto 15-Min** — BTC/ETH short-term binary markets using multi-signal fusion
6. **Weather** — NOAA forecast data vs Polymarket weather markets

## Reference Repositories (Reviewed & Validated)

These are vetted open-source repos that inform our architecture:

| Repo | Purpose | Stars | Notes |
|------|---------|-------|-------|
| `Polymarket/agents` | AI agent framework skeleton | 3.4k | MIT, CLI + ChromaDB + Gamma client |
| `Polymarket/poly-market-maker` | Official market maker reference | 285 | Python, Docker, bands + AMM strategies |
| `nautechsystems/nautilus_trader` | Production Rust trading engine | 10k | Stable Polymarket adapter, backtest-live parity |
| `evan-kolberg/prediction-market-backtesting` | Nautilus backtesting extension | 841 | Polymarket + Kalshi backtest framework |
| `ent0n29/polybot` | Full microservice infra (Kafka, ClickHouse, Grafana) | 606 | Java/Python, arbitrage strategy, paper mode default |
| `braedonsaunders/homerun` | Most complete all-in-one platform | 56 | 25+ strategies, Kelly sizing, React dashboard |
| `MrFadiAi/Polymarket-bot` | 4-strategy TS bot with risk management | 36 | Clean risk layering, dry-run toggle |
| `echandsome/Polymarket-betting-bot` | Copy trading + odds-based strategies | 90 | TypeScript, MongoDB, encrypted keys |
| `aulekator/Polymarket-BTC-15-Minute-Trading-Bot` | BTC 15m NautilusTrader bot | 257 | Multi-signal fusion, self-learning weights |

## Security Warnings

- **NEVER run code from `dev-protocol` GitHub org** — hijacked in Feb 2026, pushes key-stealing malware
- **Beware typosquatted npm packages** — always audit `package.json` dependencies before `npm install`
- **Never commit `.env` files** — private key exfiltration is the #1 attack vector in this space
- **Rotate keys immediately** if you ever ran untrusted Polymarket bot code
- **Audit all dependencies** — legit repos can be compromised via dependency chain attacks
- **Do NOT use pmxt** — unofficial package requiring insecure Node.js sidecar; removed for security (see docs/security_audit.md)

## Project Status (Updated 2026-05-15)

**All 5 phases complete.** 53 tests passing. Shadow mode verified (4/6 strategies firing).

### Completed (Session 1 -- 2026-05-14)
- Market making signal cooldown (15s per market, configurable)
- 30-min shadow run: 40,775 data points, 8,106 signals (stable, no crashes)
- React frontend + FastAPI backend verified end-to-end
- Docker deployment: `Dockerfile`, `docker-compose.yml`, `.dockerignore`
- Render deployment: `render.yaml`
- Todo checkboxes updated (walk-forward + rate limiting marked done)

### Completed (Session 2 -- 2026-05-15)
- **copy_trading**: Added momentum-following logic (was dead stub returning empty signals)
- **arbitrage**: Added mean-reversion arb for extreme prices (>=0.95, <=0.05) and large price swings
- **Cooldowns**: arbitrage (60s), copy_trading (30s), market_making (15s)
- **8 new tests**: arbitrage mean-reversion, cooldown, copy_trading momentum, target qualification
- **Shadow mode**: 4/6 strategies now fire live (arb: 59, copy: 38, mm: 576, crypto: 104)
- **Pushed to GitHub**: 2 commits pushed to origin/main

### Completed (Session 3 -- 2026-05-15)
- **CLOB API auth in data_loader**: Authenticated OHLCV via py-clob-client when API key available; pseudo-timeseries fallback from Gamma trades
- **DataFeed metadata enrichment**: `prefetch_metadata()` queries Gamma API for question/condition_id; WS-parsed MarketData now includes question text
- **Weather strategy fixes**: Auto-detect rain markets (not just temperature), forecast key migration from asset_id to condition_id, dual-key forecasts in shadow_run
- **Shadow run improvements**: `--include-weather` now caches market metadata; weather forecasts keyed by both condition_id and token_id
- **AI agent improvements**: LLM estimate TTL (30min expiry + re-evaluation), reusable OpenAI client (no per-call instantiation), structured heuristic logging
- **Deployment hardening**: Multi-stage Docker build (no Node.js in runtime image), HEALTHCHECK in Dockerfile, Prometheus volume persistence, expanded .dockerignore, render.yaml with all secrets + risk limits
- **9 new tests**: weather auto-detect (temp/rain/non-weather), forecast key migration, data_feed metadata enrichment (snapshot/price-change/fallback), AI estimate staleness, AI heuristic logging

### Quick Commands
```bash
cd C:\Users\Navar\Projects\polymarket-edge
python -m pytest tests/ -v                    # Run 53 tests
python scripts/run_backtest.py                # Synthetic backtest
python scripts/run_live_backtest.py            # Live data backtest (Gamma API)
python scripts/run_backtest.py --walk-forward  # Walk-forward validation
python scripts/shadow_run.py --duration 300 --top-markets 20  # Shadow mode
uvicorn src.dashboard.app:app --port 8000     # Backend only
cd src/dashboard/frontend && npm run dev      # Frontend dev (proxies to :8000)
docker-compose up --build                     # Full stack (needs Docker)
```

### Remaining Work (Prioritized)
1. **CLOB API key setup** -- code ready for authenticated OHLCV (via py-clob-client); just need `POLYMARKET_API_KEY` in `.env` to enable
2. **Weather strategy verification** -- code fixes in place (metadata enrichment + auto-detect rain); verify with `--include-weather` flag on next shadow run
3. **AI agent LLM mode** -- heuristic works well; needs `OPENAI_API_KEY` for LLM-based estimation (estimate TTL + client reuse in place)
4. **Docker deploy test** -- multi-stage build ready; test on Docker-enabled host
5. **Render deploy** -- render.yaml now has complete env vars; connect GitHub repo + set secrets in Render dashboard
6. **Strategy tuning with real data:**
   - copy_trading momentum: verify on real trending markets (crypto events)
   - arbitrage mean-reversion: check if PM extreme prices actually revert
   - weather: validate with `--include-weather` after metadata enrichment fix

### Deployment Notes
- **Docker**: `docker-compose up --build` starts app (:8000) + Postgres + Redis + Grafana (:3001) + Prometheus (:9090)
- **Render**: Connect GitHub repo, set env vars in Render dashboard, `render.yaml` auto-provisions DB + Redis
- **Frontend**: Built to `src/dashboard/frontend/dist/` — FastAPI serves it in production via SPA catch-all
- **No Docker locally**: This dev machine doesn't have Docker; test deployment on a cloud host

### Resuming Steps
When picking up this project:
1. `python -m pytest tests/ -v` — verify all green
2. Check `tasks/todo.md` for remaining unchecked items
3. Check `tasks/lessons.md` for past corrections
4. If working on strategies: each lives in `src/strategies/<name>/`
5. If working on dashboard: `uvicorn` backend + `npm run dev` frontend
6. If deploying: Dockerfile + docker-compose.yml ready; render.yaml for PaaS

```
polymarket-edge/
├── CLAUDE.md
├── .env.example              # Template only — never commit real .env
├── .gitignore
├── docker-compose.yml
├── pyproject.toml
├── src/
│   ├── core/                 # Shared: data pipeline, risk manager, execution engine
│   │   ├── data_feed.py      # Unified WebSocket + REST market data
│   │   ├── risk_manager.py   # Position limits, circuit breakers, daily loss caps
│   │   ├── executor.py       # Order routing (NautilusTrader adapter)
│   │   └── config.py         # Pydantic settings from .env
│   ├── strategies/
│   │   ├── arbitrage/        # Internal + cross-platform arb
│   │   ├── market_making/    # Bands + AMM on CLOB
│   │   ├── copy_trading/     # On-chain wallet follower
│   │   ├── ai_agent/         # LLM news -> probability -> trade
│   │   ├── crypto_15m/       # BTC/ETH short-term binary
│   │   └── weather/          # NOAA vs weather markets
│   ├── backtesting/          # NautilusTrader backtest harness
│   └── dashboard/            # FastAPI + React monitoring UI
├── tests/
├── docs/
└── scripts/
```

## Development Rules

- All strategies must implement a common `Strategy` interface with `on_data()`, `on_fill()`, `start()`, `stop()`
- Every trade must pass through `risk_manager.approve()` before execution
- Paper mode is the default; `LIVE_MODE=true` in `.env` enables real execution
- Shadow mode (`SHADOW_MODE=true`) uses live data feeds but paper-trades only
- All network calls must have timeouts and retry logic with exponential backoff
- Logging uses structured JSON format for Grafana ingestion
- No strategy may exceed the global position limit or daily loss cap
- All prices handled as `Decimal`, never `float`


# Workflow Orchestration

## 1. Plan Node Default

- Enter plan mode for ANY non-trivial task (3+ steps or architectural decisions)
- If something goes sideways, STOP and re-plan immediately – don’t keep pushing
- Use plan mode for verification steps, not just building
- Write detailed specs upfront to reduce ambiguity

## 2. Subagent Strategy

- Use subagents liberally to keep main context window clean
- Offload research, exploration, and parallel analysis to subagents
- For complex problems, throw more compute at it via subagents
- One tack per subagent for focused execution

## 3. Self-Improvement Loop

- After ANY correction from the user: update `tasks/lessons.md` with the pattern
- Write rules for yourself that prevent the same mistake
- Ruthlessly iterate on these lessons until mistake rate drops
- Review lessons at session start for relevant project

## 4. Verification Before Done

- Never mark a task complete without proving it works
- Diff behavior between main and your changes when relevant
- Ask yourself: “Would a staff engineer approve this?”
- Run tests, check logs, demonstrate correctness

## 5. Demand Elegance (Balanced)

- For non-trivial changes: pause and ask “is there a more elegant way?”
- If a fix feels hacky: “Knowing everything I know now, implement the elegant solution”
- Skip this for simple, obvious fixes – don’t over-engineer
- Challenge your own work before presenting it

## 6. Autonomous Bug Fixing

- When given a bug report: just fix it. Don’t ask for hand-holding
- Point at logs, errors, failing tests – then resolve them
- Zero context switching required from the user
- Go fix failing CI tests without being told how

-----

# Task Management

1. **Plan First**: Write plan to `tasks/todo.md` with checkable items
1. **Verify Plan**: Check in before starting implementation
1. **Track Progress**: Mark items complete as you go
1. **Explain Changes**: High-level summary at each step
1. **Document Results**: Add review section to `tasks/todo.md`
1. **Capture Lessons**: Update `tasks/lessons.md` after corrections

-----

# Core Principles

- **Simplicity First**: Make every change as simple as possible. Impact minimal code.
- **No Laziness**: Find root causes. No temporary fixes. Senior developer standards.
- **Minimal Impact**: Changes should only touch what’s necessary. Avoid introducing bugs.