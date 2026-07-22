"""
Entry point.

Usage:
    python main.py                 # runs in whatever mode config.settings.system_config.mode is set to
    python main.py --mode PAPER    # override mode from CLI
    python main.py --mode LIVE

Ctrl+C for a graceful shutdown (flattens no positions automatically —
open positions are left as-is and will continue to be managed if you
restart; use the kill switch for an immediate flatten-everything stop).
"""
import argparse
import sys

from config import settings
from core.engine import TradingEngine
from utils.logger import get_logger

logger = get_logger("main")


def main():
    parser = argparse.ArgumentParser(description="Autonomous Indian equities trading bot")
    parser.add_argument("--mode", choices=["PAPER", "LIVE", "BACKTEST"], default=None,
                         help="Override config.system_config.mode")
    args = parser.parse_args()

    if args.mode:
        settings.system_config.mode = args.mode

    if settings.system_config.mode == "LIVE":
        confirm = input(
            "You are about to start LIVE trading with real capital.\n"
            "Type 'CONFIRM LIVE' to proceed: "
        )
        if confirm.strip() != "CONFIRM LIVE":
            print("Aborted.")
            sys.exit(0)

    engine = TradingEngine()
    try:
        engine.run()
    except KeyboardInterrupt:
        logger.info("Shutdown requested via Ctrl+C.")
        engine.stop()


if __name__ == "__main__":
    main()
