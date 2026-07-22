"""Maps config strategy names -> instantiated Strategy objects. Add a new
strategy by writing the class + registering it here + adding its name to
config.settings.strategy_config.enabled_strategies. No other code changes."""
from typing import List

from config.settings import strategy_config
from strategies.base import Strategy
from strategies.ema_supertrend import EmaSupertrendStrategy
from strategies.vwap_breakout import VwapBreakoutStrategy
from strategies.rsi_macd_confluence import RsiMacdConfluenceStrategy
from strategies.opening_range_breakout import OpeningRangeBreakoutStrategy

_REGISTRY = {
    "ema_supertrend": EmaSupertrendStrategy,
    "vwap_breakout": VwapBreakoutStrategy,
    "rsi_macd_confluence": RsiMacdConfluenceStrategy,
    "opening_range_breakout": OpeningRangeBreakoutStrategy,
}


def load_enabled_strategies() -> List[Strategy]:
    strategies = []
    for name in strategy_config.enabled_strategies:
        cls = _REGISTRY.get(name)
        if cls is None:
            raise ValueError(f"Unknown strategy '{name}' in config. Available: {list(_REGISTRY)}")
        strategies.append(cls())
    return strategies
