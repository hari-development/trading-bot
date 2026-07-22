"""
WebSocket Server for the Flutter Dashboard.
Runs in a background thread, streaming real-time status and logs.
"""
import asyncio
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Set

import websockets

import os
from core.models import Direction
from utils.logger import get_logger, register_trade_event_callback

logger = get_logger("dashboard_server")


class DashboardServer:
    def __init__(self, engine, host: str = "0.0.0.0", port: int = 8765):
        self.engine = engine
        self.host = host
        env_port = os.environ.get("PORT")
        self.port = int(env_port) if env_port else port
        self.clients: Set[websockets.WebSocketServerProtocol] = set()
        self.loop = None
        self.thread = None
        self.server = None

    def start(self):
        """Start the WebSocket server in a background daemon thread."""
        logger.info(f"Starting dashboard server on ws://{self.host}:{self.port}")
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        register_trade_event_callback(self.broadcast_trade_event)

    def _run_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._start_server())
        self.loop.create_task(self._periodic_status_broadcast())
        self.loop.run_forever()

    async def _start_server(self):
        async def handler(websocket):
            self.clients.add(websocket)
            logger.info(f"Dashboard client connected. Total clients: {len(self.clients)}")
            try:
                await websocket.send(json.dumps(self.get_initial_state(), default=str))
                async for message in websocket:
                    try:
                        data = json.loads(message)
                        if data.get("action") == "emergency_stop":
                            reason = data.get("reason", "manual_kill_switch")
                            logger.critical(
                                f"Emergency stop requested from dashboard: {reason}"
                            )
                            self.engine.risk_manager.emergency_stop(
                                f"dashboard_kill_switch: {reason}"
                            )
                            await self.broadcast_status_update()
                    except json.JSONDecodeError:
                        logger.warning(f"Malformed JSON from client: {message}")
                    except Exception as e:
                        logger.error(f"Error handling client message: {e}")
            except websockets.exceptions.ConnectionClosed:
                pass
            finally:
                self.clients.discard(websocket)
                logger.info(
                    f"Dashboard client disconnected. Total clients: {len(self.clients)}"
                )

        self.server = await websockets.serve(handler, self.host, self.port)

    def stop(self):
        if self.loop and self.server:
            self.loop.call_soon_threadsafe(self.server.close)
            logger.info("Dashboard server stopped.")

    # ---------------------------------------------------------------- serialisation

    def serialize_position(self, position) -> dict:
        """
        Serialize an open position for the dashboard.
        For option positions, P&L is calculated using the option's own LTP.
        For equity/futures positions, the instrument's LTP is used directly.
        """
        try:
            # Always use the option's own market price for P&L display
            ltp = self.engine.broker.get_ltp(position.symbol)
        except Exception:
            ltp = position.entry_price

        if position.direction == Direction.LONG:
            pnl = (ltp - position.entry_price) * position.quantity
        else:
            pnl = (position.entry_price - ltp) * position.quantity

        cost = position.entry_price * position.quantity
        pnl_pct = (pnl / cost * 100) if cost > 0 else 0.0

        return {
            "symbol": position.symbol,
            "direction": position.direction.value,
            "entry_price": position.entry_price,
            "current_price": ltp,
            "quantity": position.quantity,
            "stop_loss": position.stop_loss,
            "take_profit": position.take_profit,
            "underlying_symbol": position.underlying_symbol,
            "underlying_sl": position.underlying_stop_loss,
            "underlying_tp": position.underlying_take_profit,
            "entry_time": position.entry_time.isoformat(),
            "strategy_name": position.strategy_name,
            "unrealized_pnl": round(pnl, 2),
            "unrealized_pnl_pct": round(pnl_pct, 2),
            "breakeven_applied": position.breakeven_applied,
            "partial_booked": position.partial_booked,
        }

    def get_recent_events(self, limit: int = 50) -> list:
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
            logger.error(f"Error loading recent events: {e}")
        events.reverse()
        return events

    def get_trade_history(self, limit: int = 20) -> list:
        """Return the last N closed trades for the dashboard trade history panel."""
        trades = getattr(self.engine, "closed_trades", [])
        return list(reversed(trades[-limit:]))

    def get_initial_state(self) -> dict:
        status = self.engine.risk_manager.status_summary()
        positions = [
            self.serialize_position(pos)
            for pos in list(self.engine.open_positions.values())
        ]
        return {
            "type": "INITIAL_STATE",
            "status": status,
            "positions": positions,
            "recent_events": self.get_recent_events(50),
            "trade_history": self.get_trade_history(20),
            "system_mode": self.engine.broker.__class__.__name__
            .replace("Broker", "")
            .upper(),
        }

    def get_status_update(self) -> dict:
        status = self.engine.risk_manager.status_summary()
        positions = [
            self.serialize_position(pos)
            for pos in list(self.engine.open_positions.values())
        ]
        return {
            "type": "STATUS_UPDATE",
            "status": status,
            "positions": positions,
            "trade_history": self.get_trade_history(20),
        }

    # ---------------------------------------------------------------- broadcast

    async def broadcast_status_update(self):
        if not self.clients or not self.loop or not self.loop.is_running():
            return
        msg = json.dumps(self.get_status_update(), default=str)
        await asyncio.gather(
            *[client.send(msg) for client in self.clients], return_exceptions=True
        )

    async def _send_to_all(self, msg: str):
        if self.clients:
            await asyncio.gather(
                *[client.send(msg) for client in self.clients], return_exceptions=True
            )

    def broadcast_trade_event(self, event_record: dict):
        """Synchronously called from logger.py when a trade event is appended."""
        if not self.clients or not self.loop or not self.loop.is_running():
            return
        msg = json.dumps({"type": "TRADE_EVENT", "event": event_record}, default=str)
        try:
            asyncio.run_coroutine_threadsafe(self._send_to_all(msg), self.loop)
        except Exception as e:
            logger.error(f"Error in broadcast_trade_event: {e}")

    async def _periodic_status_broadcast(self):
        while True:
            await asyncio.sleep(2.0)
            try:
                await self.broadcast_status_update()
            except Exception as e:
                logger.error(f"Error in periodic status broadcast: {e}")
