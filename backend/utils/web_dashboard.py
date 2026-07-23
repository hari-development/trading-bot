"""
FastAPI Dashboard Server — Phase 12
Replaces the old simple WebSocket server with a robust REST + WS API.
"""
import asyncio
import json
import logging
from typing import Any, Dict, List
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn
import threading

from core.trade_journal import trade_journal
from core.adaptive_engine import adaptive_engine

logger = logging.getLogger("web_dashboard")

app = FastAPI(title="AlgoTrader Pro API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global reference to the trading engine
_engine = None

# WebSocket Connection Manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"Dashboard WS connected. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"Dashboard WS disconnected. Total: {len(self.active_connections)}")

    async def broadcast(self, message: str):
        for connection in list(self.active_connections):
            try:
                await connection.send_text(message)
            except Exception as e:
                logger.error(f"Error broadcasting to WS: {e}")
                self.disconnect(connection)

manager = ConnectionManager()


@app.get("/api/status")
def get_status() -> Dict[str, Any]:
    if not _engine:
        return {"error": "Engine not attached"}
    return _engine.risk_manager.status_summary()


@app.get("/api/positions")
def get_positions() -> List[Dict[str, Any]]:
    if not _engine:
        return []
    # Using the serialization logic from the old dashboard
    positions = []
    for pos in list(_engine.open_positions.values()):
        try:
            ltp = _engine.broker.get_ltp(pos.symbol)
        except Exception:
            ltp = pos.entry_price

        if pos.direction.value == "LONG":
            pnl = (ltp - pos.entry_price) * pos.quantity
        else:
            pnl = (pos.entry_price - ltp) * pos.quantity

        cost = pos.entry_price * pos.quantity
        pnl_pct = (pnl / cost * 100) if cost > 0 else 0.0

        positions.append({
            "symbol": pos.symbol,
            "direction": pos.direction.value,
            "entry_price": pos.entry_price,
            "current_price": ltp,
            "quantity": pos.quantity,
            "stop_loss": pos.stop_loss,
            "take_profit": pos.take_profit,
            "entry_time": pos.entry_time.isoformat(),
            "strategy_name": pos.strategy_name,
            "unrealized_pnl": round(pnl, 2),
            "unrealized_pnl_pct": round(pnl_pct, 2),
            "confidence_score": getattr(pos, "confidence_score", 0.0),
            "trade_score": getattr(pos, "trade_score", 0),
        })
    return positions


@app.get("/api/trades")
def get_recent_trades(limit: int = 20) -> List[Dict[str, Any]]:
    if not _engine:
        return []
    trades = getattr(_engine, "closed_trades", [])
    return list(reversed(trades[-limit:]))


@app.get("/api/performance")
def get_performance() -> Dict[str, Any]:
    return adaptive_engine.get_performance_report()


@app.get("/api/journal")
def get_journal(limit: int = 50) -> List[Dict[str, Any]]:
    return trade_journal.query(limit=limit)


@app.get("/api/regime")
def get_regime() -> Dict[str, Any]:
    if not _engine or not hasattr(_engine, "current_regime"):
        return {"regime": "UNKNOWN", "confidence": 0.0}
    r = _engine.current_regime
    if r:
        return {
            "regime": r.regime.value,
            "adx": r.adx_value,
            "atr_pct": r.atr_pct,
            "confidence": getattr(r, "regime_confidence", 0.0)
        }
    return {"regime": "UNKNOWN", "confidence": 0.0}


def get_recent_events(limit: int = 50) -> List[Dict[str, Any]]:
    events_path = Path("logs/trade_events.jsonl")
    if not events_path.exists():
        return []
    events = []
    try:
        with open(events_path, "r") as f:
            lines = f.readlines()
        for line in lines[-limit:]:
            line_str = line.strip()
            if line_str:
                try:
                    events.append(json.loads(line_str))
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"Failed to read events: {e}")
    return events


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    # Send initial state immediately
    if _engine:
        init_state = {
            "type": "INITIAL_STATE",
            "status": get_status(),
            "positions": get_positions(),
            "trade_history": get_recent_trades(),
            "regime": get_regime(),
            "system_mode": _engine.broker.__class__.__name__.replace("Broker", "").upper(),
            "recent_events": get_recent_events(),
        }
        await websocket.send_text(json.dumps(init_state, default=str))

    try:
        while True:
            data = await websocket.receive_text()
            try:
                payload = json.loads(data)
                if payload.get("action") == "emergency_stop":
                    reason = payload.get("reason", "dashboard_kill_switch")
                    logger.critical(f"Emergency stop requested: {reason}")
                    if _engine:
                        _engine.risk_manager.emergency_stop(f"dashboard_kill_switch: {reason}")
                    # Broadcast update immediately
                    await manager.broadcast(json.dumps({
                        "type": "STATUS_UPDATE",
                        "status": get_status(),
                        "positions": get_positions(),
                        "regime": get_regime(),
                    }, default=str))
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        manager.disconnect(websocket)


async def _periodic_broadcast():
    """Broadcasts state to all WS clients every 2 seconds"""
    while True:
        await asyncio.sleep(2.0)
        if manager.active_connections and _engine:
            msg = json.dumps({
                "type": "STATUS_UPDATE",
                "status": get_status(),
                "positions": get_positions(),
                "trade_history": get_recent_trades(),
                "regime": get_regime(),
            }, default=str)
            await manager.broadcast(msg)


def broadcast_trade_event(event_record: dict):
    """Called by logger when an event happens"""
    if not manager.active_connections:
        return
    msg = json.dumps({"type": "TRADE_EVENT", "event": event_record}, default=str)
    # Fire and forget into the event loop
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(manager.broadcast(msg), loop)
    except Exception as e:
        logger.error(f"Failed to broadcast trade event: {e}")


class DashboardServer:
    def __init__(self, engine, host: str = "0.0.0.0", port: int = 8765):
        global _engine
        self.engine = engine
        _engine = engine
        self.host = host
        self.port = port
        self.thread = None
        self.server = None

    def start(self):
        logger.info(f"Starting FastAPI dashboard server on http://{self.host}:{self.port}")
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        
        # Register the trade event callback
        from utils.logger import register_trade_event_callback
        register_trade_event_callback(broadcast_trade_event)

    def _run(self):
        config = uvicorn.Config(app, host=self.host, port=self.port, log_level="warning")
        self.server = uvicorn.Server(config)
        
        # Override the server's startup to start the background broadcast task
        original_startup = self.server.startup
        async def startup_wrapper(*args, **kwargs):
            await original_startup(*args, **kwargs)
            asyncio.create_task(_periodic_broadcast())
        
        self.server.startup = startup_wrapper
        self.server.run()

    def stop(self):
        if self.server:
            self.server.should_exit = True
            logger.info("FastAPI dashboard server stopped.")
