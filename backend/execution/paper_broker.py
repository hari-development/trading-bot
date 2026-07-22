"""
Paper Broker — simulates order execution against real (or historical)
market data with no capital at risk. Behaves like the live broker
interface exactly, so switching config.system_config.mode from PAPER to
LIVE requires zero changes to strategy/risk/trade-management code.

Data source here defaults to yfinance for NSE symbols (free, no API key)
so paper trading works out of the box before Kite API keys are set up.
For live trading, swap to execution/kite_broker.py.
"""
import uuid
from typing import Dict, Optional

import pandas as pd

from core.models import Direction
from execution.broker_base import Broker
from utils.logger import get_logger

logger = get_logger("paper_broker")

try:
    import yfinance as yf
    _YF_AVAILABLE = True
except ImportError:
    _YF_AVAILABLE = False


NSE_SUFFIX = ".NS"
_TIMEFRAME_MAP = {"1minute": "1m", "5minute": "5m", "15minute": "15m", "60minute": "60m", "day": "1d"}



# Period to request from yfinance for each interval
# 60m needs at least 30d to get enough bars for regime classification
_PERIOD_MAP: dict[str, str] = {
    "1m":  "5d",
    "5m":  "5d",
    "15m": "5d",
    "60m": "30d",
    "1d":  "6mo",
}


def _parse_simulated_option(symbol: str) -> tuple:
    """
    Parses an underscore-separated simulated option symbol to extract underlying, strike, and type.
    Example: NIFTY_26JUL09_22150_CE -> NIFTY, 22150.0, CE
    """
    parts = symbol.split("_")
    underlying = parts[0]
    strike = float(parts[2])
    option_type = parts[3]
    return underlying, strike, option_type


class PaperBroker(Broker):
    def __init__(self, starting_capital: float = 10000.0):
        self.cash = starting_capital
        self.order_log: list = []
        self._connected = True
        self._price_cache = {}  # symbol -> (base_price, last_updated_time)
        self._sim_price = {}    # symbol -> current_simulated_price

    def is_connected(self) -> bool:
        return self._connected

    def get_historical_data(self, symbol: str, timeframe: str = "5minute", lookback_bars: int = 200) -> pd.DataFrame:
        if not _YF_AVAILABLE:
            raise RuntimeError(
                "yfinance not installed. Run: pip install yfinance --break-system-packages"
            )
        yf_interval = _TIMEFRAME_MAP.get(timeframe, "5m")
        period = _PERIOD_MAP.get(yf_interval, "5d")

        # Use verified index symbols; fall back to SYMBOL.NS for stocks
        yf_symbol = YF_SYMBOL_MAP.get(symbol, symbol + NSE_SUFFIX)

        ticker = yf.Ticker(yf_symbol)
        df = ticker.history(period=period, interval=yf_interval)
        if df.empty:
            logger.debug(f"No {timeframe} data from yfinance for {symbol} ({yf_symbol}). Skipping.")
            return df
        df = df.rename(columns={"Open": "open", "High": "high", "Low": "low",
                                 "Close": "close", "Volume": "volume"})
        df = df[["open", "high", "low", "close", "volume"]].tail(lookback_bars)
        df.index.name = "datetime"
        # Ensure volume column exists and has no NaN
        if "volume" not in df.columns:
            df["volume"] = 0
        else:
            df["volume"] = df["volume"].fillna(0)
        return df

    def get_ltp(self, symbol: str) -> float:
        import time
        import random

        if symbol.endswith("CE") or symbol.endswith("PE"):
            underlying, strike, option_type = _parse_simulated_option(symbol)
            underlying_ltp = self.get_ltp(underlying)
            
            # Simulated pricing model (Intrinsic + 1.5% Extrinsic value)
            intrinsic = max(0.0, underlying_ltp - strike) if option_type == "CE" else max(0.0, strike - underlying_ltp)
            extrinsic = 0.015 * underlying_ltp
            return round(intrinsic + extrinsic, 2)

        now = time.time()
        cached = self._price_cache.get(symbol)
        
        # Limit Yahoo Finance HTTP requests to once every 30 seconds per symbol
        if not cached or (now - cached[1]) > 30.0:
            yf_symbol = YF_SYMBOL_MAP.get(symbol, symbol + NSE_SUFFIX)
            try:
                df = self.get_historical_data(symbol, "1minute", lookback_bars=1)
                if df.empty:
                    base_price = self._sim_price.get(symbol, 100.0)
                else:
                    base_price = float(df["close"].iloc[-1])
            except Exception:
                base_price = self._sim_price.get(symbol, 100.0)
            
            self._price_cache[symbol] = (base_price, now)
            if symbol not in self._sim_price:
                self._sim_price[symbol] = base_price
            else:
                # Smooth transition to new Yahoo Finance base price
                self._sim_price[symbol] = 0.8 * self._sim_price[symbol] + 0.2 * base_price

        # Apply a tiny random walk (up to 0.04% fluctuation per tick) to simulate real-time feed
        change_pct = random.uniform(-0.0004, 0.0004)
        self._sim_price[symbol] *= (1 + change_pct)
        return round(self._sim_price[symbol], 2)

    def place_order(self, symbol: str, direction: Direction, quantity: int,
                     order_type: str = "MARKET", price: Optional[float] = None) -> str:
        order_id = f"PAPER-{uuid.uuid4().hex[:10]}"
        fill_price = price if price is not None else self.get_ltp(symbol)
        record = {
            "order_id": order_id, "symbol": symbol, "direction": direction.value,
            "quantity": quantity, "order_type": order_type, "fill_price": fill_price,
        }
        self.order_log.append(record)
        logger.info(f"[PAPER FILL] {direction.value} {quantity} {symbol} @ {fill_price:.2f} (order {order_id})")
        return order_id

    def get_option_contract(self, underlying_symbol: str, option_type: str, underlying_price: float, strike_selection: str = "ITM1") -> dict:
        """
        Generates a simulated closest expiry option contract (ITM1 / ATM / OTM1).
        """
        # Determine strike step based on index/stock
        if "BANKNIFTY" in underlying_symbol:
            step = 100
        elif "MIDCPNIFTY" in underlying_symbol or "FINNIFTY" in underlying_symbol or "NIFTY" in underlying_symbol or "SENSEX" in underlying_symbol:
            step = 50
        else:
            # Stock step (approx 1% of stock price rounded to nearest 5)
            step = max(5, int(round(underlying_price * 0.01 / 5) * 5))
            
        atm_strike = float(round(underlying_price / step) * step)

        if strike_selection == "ITM1":
            strike = (atm_strike - step) if option_type == "CE" else (atm_strike + step)
        elif strike_selection == "OTM1":
            strike = (atm_strike + step) if option_type == "CE" else (atm_strike - step)
        else:
            strike = atm_strike
        
        # Calculate next Thursday expiry
        import datetime as dt
        today = dt.date.today()
        days_ahead = 3 - today.weekday()
        if days_ahead < 0:
            days_ahead += 7
        next_thursday = today + dt.timedelta(days=days_ahead)
        expiry_str = next_thursday.strftime("%Y-%m-%d")
        expiry_symbol_str = next_thursday.strftime("%y%b%d").upper()
        
        tradingsymbol = f"{underlying_symbol}_{expiry_symbol_str}_{int(strike)}_{option_type}"
        
        return {
            "tradingsymbol": tradingsymbol,
            "strike": strike,
            "expiry": expiry_str,
        }

