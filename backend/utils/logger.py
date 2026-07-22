"""Centralized logging: one rotating file per component + a dedicated
structured trade log (JSON lines) for entry/exit reasons, indicator
snapshots, and P&L — everything the dashboard and post-mortems need."""
import json
import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path("logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

_configured_loggers = {}


def get_logger(name: str) -> logging.Logger:
    if name in _configured_loggers:
        return _configured_loggers[name]

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    fmt = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")

    file_handler = RotatingFileHandler(LOG_DIR / f"{name}.log", maxBytes=5_000_000, backupCount=5)
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    console_handler.setLevel(logging.INFO)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    _configured_loggers[name] = logger
    return logger


_trade_event_callbacks = []


def register_trade_event_callback(callback):
    """Register a callback to receive trade events in real-time."""
    _trade_event_callbacks.append(callback)


def log_trade_event(event_type: str, payload: dict):
    """Append a structured JSON-line record. event_type: SIGNAL_REJECTED |
    ENTRY | EXIT | RISK_EVENT | ERROR. This file is what the Flutter
    dashboard and backtester's trade-by-trade view read from."""
    record = {"timestamp": datetime.now().isoformat(), "event_type": event_type, **payload}
    trade_log_path = LOG_DIR / "trade_events.jsonl"
    with open(trade_log_path, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")

    # Notify any registered callbacks (e.g. WebSocket server)
    for cb in _trade_event_callbacks:
        try:
            cb(record)
        except Exception as e:
            pass

