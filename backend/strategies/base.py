"""
Strategy interface. Every strategy is modular and interchangeable —
the engine only knows about this contract, never a concrete strategy.
This is what makes strategies pluggable via config.enabled_strategies.
"""
from abc import ABC, abstractmethod
from typing import Optional

import pandas as pd

from core.models import Signal


class Strategy(ABC):
    name: str = "base"

    @abstractmethod
    def evaluate(self, symbol: str, df: pd.DataFrame) -> Optional[Signal]:
        """
        Given OHLCV history (most recent bar last), return a Signal if
        this strategy's entry conditions are met, else None.
        Must NOT place orders itself — pure decision function.
        """
        raise NotImplementedError

    def required_lookback(self) -> int:
        """Minimum number of bars needed to evaluate. Override per strategy."""
        return 50
