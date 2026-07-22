"""Abstract broker interface. PaperBroker and KiteBroker both implement
this so the engine never cares which one it's talking to."""
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

import pandas as pd

from core.models import Direction


class Broker(ABC):
    @abstractmethod
    def get_historical_data(self, symbol: str, timeframe: str, lookback_bars: int) -> pd.DataFrame:
        """Return OHLCV dataframe indexed by datetime, columns: open, high, low, close, volume."""
        raise NotImplementedError

    @abstractmethod
    def get_ltp(self, symbol: str) -> float:
        raise NotImplementedError

    @abstractmethod
    def place_order(self, symbol: str, direction: Direction, quantity: int,
                     order_type: str = "MARKET", price: Optional[float] = None) -> str:
        """Returns a broker order id."""
        raise NotImplementedError

    @abstractmethod
    def is_connected(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def get_option_contract(self, underlying_symbol: str, option_type: str, underlying_price: float) -> dict:
        """
        Resolves an option contract for a given underlying symbol.
        Returns a dictionary representing the option contract details:
        {
            "tradingsymbol": str,
            "strike": float,
            "expiry": str,
        }
        """
        raise NotImplementedError

