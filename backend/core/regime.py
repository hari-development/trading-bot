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
    LOW_VOLATILITY = "LOW_VOLATILITY"   # New: tight consolidation
    GAP_UP = "GAP_UP"                   # New: session opened with significant gap up
    GAP_DOWN = "GAP_DOWN"               # New: session opened with significant gap down
    NEWS_DRIVEN = "NEWS_DRIVEN"         # New: abnormal ATR spike in first 30 min
    UNKNOWN = "UNKNOWN"


@dataclass
class RegimeSnapshot:
    regime: Regime
    adx_value: float
    atr_pct: float
    plus_di: float
    minus_di: float
    regime_confidence: float = 1.0   # 0.0-1.0: how certain the classification is
    prev_close: float = 0.0          # prior session close (for gap detection)

    @property
    def is_tradeable(self) -> bool:
        """Regimes that are outright dangerous for any strategy."""
        return self.regime not in (Regime.HIGH_VOLATILITY, Regime.NEWS_DRIVEN)


def classify_regime(df: pd.DataFrame, adx_period: int = 14,
                     adx_trend_threshold: float = 22.0,
                     high_vol_atr_pct: float = 2.5,
                     low_vol_atr_pct: float = 0.5,
                     gap_threshold_pct: float = 0.5) -> RegimeSnapshot:
    """
    Classifies market regime using ADX (trend strength/direction), ATR
    (volatility), and gap detection (session open vs prior close).

    Regime priority order:
      1. NEWS_DRIVEN  — abnormal ATR spike in first few bars (> 3.0%)
      2. HIGH_VOLATILITY — ATR% above high_vol_atr_pct
      3. GAP_UP / GAP_DOWN — session opened >gap_threshold_pct from prior close
      4. TRENDING_UP / TRENDING_DOWN — ADX above threshold
      5. RANGING — moderate ADX
      6. LOW_VOLATILITY — ATR% below low_vol_atr_pct
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

    # Gap detection — compare first bar open to prior session's last close
    gap_pct = 0.0
    prev_close = 0.0
    if len(df) >= 2:
        # Use the second-to-last session's close as prior close if index is datetime
        try:
            if isinstance(df.index, pd.DatetimeIndex) and len(df) > 1:
                today = df.index[-1].date()
                prior_bars = df[df.index.date < today]
                if not prior_bars.empty:
                    prev_close = float(prior_bars["close"].iloc[-1])
                    first_open = float(df[df.index.date == today]["open"].iloc[0])
                    gap_pct = (first_open - prev_close) / prev_close * 100 if prev_close else 0.0
        except Exception:
            pass

    confidence = 1.0

    # 1. News-driven: extremely high ATR in very early session bars
    early_bars = df.tail(6)  # last ~30 minutes at 5-min bars
    early_atr_pct = 0.0
    if not early_bars.empty and last_close > 0:
        early_range = float((early_bars["high"] - early_bars["low"]).mean())
        early_atr_pct = early_range / last_close * 100
    if early_atr_pct > 3.0 and atr_pct > 2.5:
        return RegimeSnapshot(Regime.NEWS_DRIVEN, last_adx, atr_pct, last_plus_di, last_minus_di,
                              regime_confidence=0.8, prev_close=prev_close)

    # 2. High volatility: ATR% above threshold
    if atr_pct >= high_vol_atr_pct:
        return RegimeSnapshot(Regime.HIGH_VOLATILITY, last_adx, atr_pct, last_plus_di, last_minus_di,
                              regime_confidence=0.9, prev_close=prev_close)

    # 3. Gap-up / Gap-down
    if gap_pct >= gap_threshold_pct:
        return RegimeSnapshot(Regime.GAP_UP, last_adx, atr_pct, last_plus_di, last_minus_di,
                              regime_confidence=0.9, prev_close=prev_close)
    if gap_pct <= -gap_threshold_pct:
        return RegimeSnapshot(Regime.GAP_DOWN, last_adx, atr_pct, last_plus_di, last_minus_di,
                              regime_confidence=0.9, prev_close=prev_close)

    # 4. Trending
    if last_adx >= adx_trend_threshold:
        regime = Regime.TRENDING_UP if last_plus_di > last_minus_di else Regime.TRENDING_DOWN
        confidence = min(1.0, (last_adx - adx_trend_threshold) / 20.0 + 0.5)
        return RegimeSnapshot(regime, last_adx, atr_pct, last_plus_di, last_minus_di,
                              regime_confidence=confidence, prev_close=prev_close)

    # 5. Low volatility
    if atr_pct <= low_vol_atr_pct:
        return RegimeSnapshot(Regime.LOW_VOLATILITY, last_adx, atr_pct, last_plus_di, last_minus_di,
                              regime_confidence=0.8, prev_close=prev_close)

    # 6. Ranging (default)
    return RegimeSnapshot(Regime.RANGING, last_adx, atr_pct, last_plus_di, last_minus_di,
                          regime_confidence=0.7, prev_close=prev_close)


def multi_tf_regime_consensus(regimes: dict) -> dict:
    """
    Given a dict of {timeframe: RegimeSnapshot}, computes the consensus
    regime and HTF alignment direction.

    Returns:
        {
          'consensus_regime': Regime,
          'htf_bias': 'BULLISH' | 'BEARISH' | 'NEUTRAL',
          'agreement_score': float  # 0.0-1.0
        }
    """
    if not regimes:
        return {"consensus_regime": Regime.UNKNOWN, "htf_bias": "NEUTRAL", "agreement_score": 0.0}

    bullish_count = sum(1 for r in regimes.values() if r.regime == Regime.TRENDING_UP)
    bearish_count = sum(1 for r in regimes.values() if r.regime == Regime.TRENDING_DOWN)
    total = len(regimes)

    if bullish_count > bearish_count:
        bias = "BULLISH"
        consensus = Regime.TRENDING_UP
        score = bullish_count / total
    elif bearish_count > bullish_count:
        bias = "BEARISH"
        consensus = Regime.TRENDING_DOWN
        score = bearish_count / total
    else:
        bias = "NEUTRAL"
        # Pick the most common regime
        from collections import Counter
        regime_counts = Counter(r.regime for r in regimes.values())
        consensus = regime_counts.most_common(1)[0][0]
        score = 0.5

    return {"consensus_regime": consensus, "htf_bias": bias, "agreement_score": score}
