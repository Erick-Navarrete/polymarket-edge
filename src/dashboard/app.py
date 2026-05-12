"""FastAPI dashboard backend — serves strategy state, positions, PnL, and risk metrics."""

from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import asyncio
import json

from src.core.config import get_settings
from src.core.engine import Engine

app = FastAPI(title="Polymarket Edge", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = Engine(get_settings())
    return _engine


@app.get("/")
async def root():
    return {"name": "Polymarket Edge", "version": "0.1.0", "mode": "LIVE" if get_engine().settings.live_mode else "PAPER"}


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
    """Stream real-time strategy updates to connected clients."""
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
                    }
                    for name, s in engine._strategies.items()
                },
            }
            await websocket.send_json(payload)
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        pass


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
