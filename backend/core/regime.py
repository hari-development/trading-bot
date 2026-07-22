"""
Market Regime Detection.

Every trade decision — and every post-loss recovery decision — is
conditioned on the current regime. This is the piece that stops the bot
from blindly re-entering after a loss: it must first re-classify the
market before it's allowed to trade again.
"""
from dataclasses import dataclass
from enum import Enum

import pandas as pd

from core.indicators import adx, atr


class Regime(str, Enum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    UNKNOWN = "UNKNOWN"


@dataclass
class RegimeSnapshot:
    regime: Regime
    adx_value: float
    atr_pct: float
    plus_di: float
    minus_di: float

    @property
    def is_tradeable(self) -> bool:
        """High volatility regimes are not automatically excluded, but the
        strategy layer must ask for reduced size / wider stops when this
        is False-leaning. Ranging markets are fine for mean-reversion
        strategies, so 'tradeable' here just filters out chaotic tape."""
        return self.regime != Regime.HIGH_VOLATILITY


def classify_regime(df: pd.DataFrame, adx_period: int = 14,
                     adx_trend_threshold: float = 22.0,
                     high_vol_atr_pct: float = 2.5) -> RegimeSnapshot:
    """
    Classifies market regime using ADX (trend strength/direction) and
    ATR as a % of price (volatility). This mirrors how a discretionary
    trader reads the tape: strong directional ADX -> trend; low ADX with
    contained ATR -> range; ATR blowing out -> treat as unstable
    regardless of ADX reading.
    """
    if len(df) < adx_period + 5:
        return RegimeSnapshot(Regime.UNKNOWN, 0, 0, 0, 0)

    adx_series, plus_di, minus_di = adx(df, adx_period)
    atr_series = atr(df, adx_period)

    last_close = df["close"].iloc[-1]
    last_adx = float(adx_series.iloc[-1]) if not pd.isna(adx_series.iloc[-1]) else 0.0
    last_plus_di = float(plus_di.iloc[-1]) if not pd.isna(plus_di.iloc[-1]) else 0.0
    last_minus_di = float(minus_di.iloc[-1]) if not pd.isna(minus_di.iloc[-1]) else 0.0
    last_atr = float(atr_series.iloc[-1]) if not pd.isna(atr_series.iloc[-1]) else 0.0
    atr_pct = (last_atr / last_close * 100) if last_close else 0.0

    if atr_pct >= high_vol_atr_pct:
        regime = Regime.HIGH_VOLATILITY
    elif last_adx >= adx_trend_threshold:
        regime = Regime.TRENDING_UP if last_plus_di > last_minus_di else Regime.TRENDING_DOWN
    else:
        regime = Regime.RANGING

    return RegimeSnapshot(regime, last_adx, atr_pct, last_plus_di, last_minus_di)
