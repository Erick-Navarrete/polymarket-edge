"""FastAPI dashboard backend — serves strategy state, positions, PnL, signals, and risk metrics."""

import time
from collections import deque
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, PlainTextResponse
from prometheus_client import generate_latest
import asyncio
import json

from src.core.config import get_settings
from src.core.engine import Engine

app = FastAPI(title="Polymarket Edge", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_engine: Engine | None = None
_signal_history: deque = deque(maxlen=200)
_fill_history: deque = deque(maxlen=200)
_equity_history: deque = deque(maxlen=500)

STRATEGY_META = {
    "arbitrage": {"icon": "arrows", "description": "Internal + cross-platform arbitrage"},
    "market_making": {"icon": "lines", "description": "Spread capture with inventory skew"},
    "copy_trading": {"icon": "copy", "description": "Follow profitable wallets + momentum"},
    "crypto_15m": {"icon": "btc", "description": "BTC/ETH 15-min multi-signal fusion"},
    "ai_agent": {"icon": "brain", "description": "LLM probability estimation + Kelly sizing"},
    "weather": {"icon": "cloud", "description": "NOAA forecast vs weather markets"},
}


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = Engine(get_settings())
    return _engine


def record_signal(signal) -> None:
    _signal_history.append({
        "time": time.time(),
        "strategy": signal.strategy,
        "condition_id": signal.condition_id[:16],
        "side": signal.side,
        "price": str(signal.price),
        "size": str(signal.size),
        "reason": signal.reason[:120] if signal.reason else "",
        "confidence": signal.confidence,
    })


def record_fill(signal, fill_price, fill_size) -> None:
    pnl = (signal.price - fill_price) * fill_size
    _fill_history.append({
        "time": time.time(),
        "strategy": signal.strategy,
        "side": signal.side,
        "price": str(signal.price),
        "fill_price": str(fill_price),
        "size": str(fill_size),
        "pnl": str(pnl),
    })
    _equity_history.append({
        "time": time.time(),
        "equity": str(_engine._equity + pnl) if _engine else "1000",
    })


@app.get("/")
async def root():
    engine = get_engine()
    mode = "LIVE" if engine.settings.live_mode else ("SHADOW" if engine.settings.shadow_mode else "PAPER")
    return {"name": "Polymarket Edge", "version": "0.2.0", "mode": mode}


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.2.0"}


@app.get("/api/strategies")
async def list_strategies():
    engine = get_engine()
    return {
        "strategies": [
            {
                "name": s.name,
                "state": s.state.value,
                "total_pnl": str(s.total_pnl),
                "total_exposure": str(s.total_exposure),
                "positions": len(s.positions),
                "description": STRATEGY_META.get(s.name, {}).get("description", ""),
                "icon": STRATEGY_META.get(s.name, {}).get("icon", "gear"),
            }
            for s in engine._strategies.values()
        ]
    }


@app.get("/api/risk")
async def risk_status():
    engine = get_engine()
    return engine.risk_manager.status()


@app.get("/api/positions")
async def all_positions():
    engine = get_engine()
    positions = []
    for strategy in engine._strategies.values():
        for cid, pos in strategy.positions.items():
            positions.append({
                "strategy": strategy.name,
                "condition_id": cid,
                "side": pos.side,
                "entry_price": str(pos.entry_price),
                "size": str(pos.size),
                "current_price": str(pos.current_price),
                "unrealized_pnl": str(pos.unrealized_pnl),
            })
    return {"positions": positions}


@app.get("/api/paper-fills")
async def paper_fills():
    engine = get_engine()
    return {"fills": engine.executor.paper_fills}


@app.get("/api/signals")
async def recent_signals():
    return {"signals": list(_signal_history)}


@app.get("/api/fills")
async def recent_fills():
    return {"fills": list(_fill_history)}


@app.get("/api/equity-history")
async def equity_history():
    return {"history": list(_equity_history)}


@app.post("/api/strategies/{name}/start")
async def start_strategy(name: str):
    engine = get_engine()
    strategy = engine._strategies.get(name)
    if not strategy:
        return {"error": f"Strategy '{name}' not found"}
    await strategy.start()
    return {"status": "started", "strategy": name}


@app.post("/api/strategies/{name}/stop")
async def stop_strategy(name: str):
    engine = get_engine()
    strategy = engine._strategies.get(name)
    if not strategy:
        return {"error": f"Strategy '{name}' not found"}
    await strategy.stop()
    return {"status": "stopped", "strategy": name}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Stream real-time strategy updates, signals, and fills to connected clients."""
    await websocket.accept()
    engine = get_engine()

    try:
        while True:
            payload = {
                "risk": engine.risk_manager.status(),
                "strategies": {
                    name: {
                        "state": s.state.value,
                        "pnl": str(s.total_pnl),
                        "exposure": str(s.total_exposure),
                        "positions": len(s.positions),
                        "description": STRATEGY_META.get(name, {}).get("description", ""),
                        "icon": STRATEGY_META.get(name, {}).get("icon", "gear"),
                    }
                    for name, s in engine._strategies.items()
                },
                "recent_signals": list(_signal_history)[-20:],
                "recent_fills": list(_fill_history)[-20:],
                "equity_history": list(_equity_history)[-60:],
            }
            await websocket.send_json(payload)
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        pass


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics():
    """Prometheus metrics endpoint."""
    return generate_latest().decode()


# Serve React frontend in production (built files from frontend/dist/)
_frontend_dist = Path(__file__).parent / "frontend" / "dist"

if _frontend_dist.is_dir():
    app.mount("/assets", StaticFiles(directory=_frontend_dist / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve the React SPA — fall back to index.html for client-side routing."""
        file = _frontend_dist / full_path
        if file.is_file():
            return FileResponse(file)
        return FileResponse(_frontend_dist / "index.html")
