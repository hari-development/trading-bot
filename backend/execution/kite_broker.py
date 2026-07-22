"""
Zerodha Kite Connect broker adapter — used only when
config.system_config.mode == "LIVE".

Requires: pip install kiteconnect --break-system-packages
Requires environment variables: KITE_API_KEY, KITE_API_SECRET, KITE_ACCESS_TOKEN
(access token is generated daily via the login flow — see README for the
one-time-per-day token refresh script).
"""
import os
from typing import Optional

import pandas as pd

from core.models import Direction
from execution.broker_base import Broker
from utils.logger import get_logger

logger = get_logger("kite_broker")

_TIMEFRAME_MAP = {
    "1minute": "minute", "5minute": "5minute", "15minute": "15minute", "day": "day",
}


class KiteBroker(Broker):
    def __init__(self):
        try:
            from kiteconnect import KiteConnect
        except ImportError as e:
            raise RuntimeError(
                "kiteconnect not installed. Run: pip install kiteconnect --break-system-packages"
            ) from e

        api_key = os.environ.get("KITE_API_KEY")
        access_token = os.environ.get("KITE_ACCESS_TOKEN")
        if not api_key or not access_token:
            raise RuntimeError(
                "KITE_API_KEY / KITE_ACCESS_TOKEN not set. See README deployment guide "
                "for the daily token-refresh flow (Kite access tokens expire every day)."
            )

        self.kite = KiteConnect(api_key=api_key)
        self.kite.set_access_token(access_token)
        self._instrument_cache = {}
        self._connected = self._verify_connection()

    def _verify_connection(self) -> bool:
        try:
            self.kite.profile()
            logger.info("Kite Connect authenticated successfully.")
            return True
        except Exception as e:
            logger.error(f"Kite Connect authentication failed: {e}")
            return False

    def is_connected(self) -> bool:
        return self._connected

    def _resolve_token(self, symbol: str) -> int:
        if symbol in self._instrument_cache:
            return self._instrument_cache[symbol]
            
        exchanges = ["NSE", "NFO"]
        for exchange in exchanges:
            try:
                instruments = self.kite.instruments(exchange)
                for inst in instruments:
                    target_symbol = symbol
                    if symbol == "NIFTY":
                        target_symbol = "NIFTY 50"
                    elif symbol == "BANKNIFTY":
                        target_symbol = "NIFTY BANK"
                        
                    if inst["tradingsymbol"] == target_symbol:
                        self._instrument_cache[symbol] = inst["instrument_token"]
                        return inst["instrument_token"]
            except Exception:
                continue
        raise ValueError(f"Instrument token not found for symbol {symbol}")

    def get_historical_data(self, symbol: str, timeframe: str = "5minute", lookback_bars: int = 200) -> pd.DataFrame:
        import datetime as dt
        token = self._resolve_token(symbol)
        interval = _TIMEFRAME_MAP.get(timeframe, "5minute")
        from_date = dt.datetime.now() - dt.timedelta(days=5)
        to_date = dt.datetime.now()
        data = self.kite.historical_data(token, from_date, to_date, interval)
        df = pd.DataFrame(data)
        if df.empty:
            return df
        df = df.rename(columns={"date": "datetime"}).set_index("datetime")
        # Ensure volume exists (some indices might not have it)
        if "volume" not in df.columns:
            df["volume"] = 0
        return df[["open", "high", "low", "close", "volume"]].tail(lookback_bars)

    def get_ltp(self, symbol: str) -> float:
        exchange = "NFO" if (symbol.endswith("CE") or symbol.endswith("PE")) else "NSE"
        quote = self.kite.ltp(f"{exchange}:{symbol}")
        return float(quote[f"{exchange}:{symbol}"]["last_price"])

    def place_order(self, symbol: str, direction: Direction, quantity: int,
                     order_type: str = "MARKET", price: Optional[float] = None) -> str:
        from kiteconnect import KiteConnect
        transaction_type = "BUY" if direction == Direction.LONG else "SELL"
        exchange = "NFO" if (symbol.endswith("CE") or symbol.endswith("PE")) else "NSE"
        order_params = dict(
            variety=self.kite.VARIETY_REGULAR,
            exchange=exchange,
            tradingsymbol=symbol,
            transaction_type=transaction_type,
            quantity=quantity,
            product=self.kite.PRODUCT_MIS,   # intraday margin product
            order_type=self.kite.ORDER_TYPE_MARKET if order_type == "MARKET" else self.kite.ORDER_TYPE_LIMIT,
        )
        if order_type == "LIMIT" and price is not None:
            order_params["price"] = price

        try:
            order_id = self.kite.place_order(**order_params)
            logger.info(f"[LIVE ORDER] {transaction_type} {quantity} {symbol} -> order_id={order_id}")
            return order_id
        except Exception as e:
            logger.error(f"Order placement FAILED for {symbol}: {e}")
            raise

    def get_option_contract(self, underlying_symbol: str, option_type: str, underlying_price: float, strike_selection: str = "ITM1") -> dict:
        """
        Resolves the closest expiry option contract (ITM1 / ATM / OTM1) from NFO.
        """
        try:
            instruments = self.kite.instruments("NFO")
        except Exception as e:
            logger.error(f"Failed to fetch NFO instruments: {e}")
            raise RuntimeError(f"Failed to fetch NFO instruments from Kite: {e}")

        # Filter by name (e.g. NIFTY, BANKNIFTY, RELIANCE) and type (CE/PE)
        matching = [
            inst for inst in instruments
            if inst.get("name") == underlying_symbol and inst.get("instrument_type") == option_type
        ]

        if not matching:
            # Fallback search in tradingsymbol
            matching = [
                inst for inst in instruments
                if underlying_symbol in inst.get("tradingsymbol", "") and inst.get("instrument_type") == option_type
            ]

        if not matching:
            raise ValueError(f"No option contracts found for underlying={underlying_symbol}, type={option_type}")

        # Group by expiry and find the closest active expiry
        import datetime as dt
        valid_contracts = []
        for inst in matching:
            expiry = inst.get("expiry")
            if not expiry:
                continue
            if isinstance(expiry, str):
                try:
                    expiry_date = dt.datetime.strptime(expiry, "%Y-%m-%d").date()
                except ValueError:
                    continue
            elif isinstance(expiry, dt.date):
                expiry_date = expiry
            elif isinstance(expiry, dt.datetime):
                expiry_date = expiry.date()
            else:
                continue

            if expiry_date >= dt.date.today():
                valid_contracts.append((expiry_date, inst))

        if not valid_contracts:
            raise ValueError(f"No active option contracts found for {underlying_symbol}")

        # Sort to get closest expiry
        valid_contracts.sort(key=lambda x: x[0])
        closest_expiry_date = valid_contracts[0][0]

        closest_contracts = [item[1] for item in valid_contracts if item[0] == closest_expiry_date]
        closest_contracts.sort(key=lambda x: x["strike"])

        # Find ATM index
        atm_idx = min(range(len(closest_contracts)), key=lambda i: abs(closest_contracts[i]["strike"] - underlying_price))

        target_idx = atm_idx
        if strike_selection == "ITM1":
            target_idx = max(0, atm_idx - 1) if option_type == "CE" else min(len(closest_contracts) - 1, atm_idx + 1)
        elif strike_selection == "OTM1":
            target_idx = min(len(closest_contracts) - 1, atm_idx + 1) if option_type == "CE" else max(0, atm_idx - 1)

        selected_contract = closest_contracts[target_idx]

        logger.info(f"Resolved {strike_selection} options contract for {underlying_symbol} (LTP {underlying_price}): "
                    f"{selected_contract['tradingsymbol']} (Strike {selected_contract['strike']}, Expiry {selected_contract['expiry']})")

        return {
            "tradingsymbol": selected_contract["tradingsymbol"],
            "strike": float(selected_contract["strike"]),
            "expiry": str(selected_contract["expiry"]),
        }

