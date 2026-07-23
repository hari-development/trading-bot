"""
Multi-Confirmation Library — Phase 5: Trading Intelligence.

Centralised confirmation checks shared by all strategies.
Each function takes the OHLCV DataFrame and desired Direction, and returns:
    (passes: bool, partial_score: float)

where partial_score is 0.0 (failed) or 1.0 (passed) — the WeightedConfidenceEngine
multiplies this by the indicator's configured weight to produce the final score.

Design principle: every function is pure, stateless, and independently testable.
No order-placement or broker interaction here — pure signal evaluation.
"""
from typing import Optional, Tuple

import numpy as np
import pandas as pd

from core.models import Direction
from core.indicators import (
    ema, supertrend, vwap, rsi, macd, adx, atr, avg_volume,
    bollinger_bands, obv, candle_pattern, stochastic_rsi
)


def check_ema_trend(df: pd.DataFrame, direction: Direction,
                    fast: int = 9, slow: int = 21) -> Tuple[bool, float]:
    """EMA trend alignment: fast EMA above slow EMA for LONG, below for SHORT."""
    if len(df) < slow + 5:
        return False, 0.0
    ema_fast = ema(df["close"], fast)
    ema_slow = ema(df["close"], slow)
    last_fast = float(ema_fast.iloc[-1])
    last_slow = float(ema_slow.iloc[-1])
    prev_fast = float(ema_fast.iloc[-2])
    prev_slow = float(ema_slow.iloc[-2])
    
    is_above = last_fast > last_slow
    # Give extra credit for a fresh crossover vs already established trend
    fresh_cross = (prev_fast <= prev_slow and last_fast > last_slow) or \
                  (prev_fast >= prev_slow and last_fast < last_slow)
    
    if direction == Direction.LONG:
        passes = is_above
        score = 1.0 if fresh_cross else (0.8 if is_above else 0.0)
    else:
        passes = not is_above
        score = 1.0 if fresh_cross else (0.8 if not is_above else 0.0)
    return passes, score


def check_supertrend(df: pd.DataFrame, direction: Direction,
                     period: int = 10, mult: float = 3.0) -> Tuple[bool, float]:
    """SuperTrend direction alignment."""
    if len(df) < period + 5:
        return False, 0.0
    _, st_trend = supertrend(df, period, mult)
    last_trend = int(st_trend.iloc[-1])
    if direction == Direction.LONG:
        passes = last_trend == 1
    else:
        passes = last_trend == -1
    return passes, 1.0 if passes else 0.0


def check_vwap_position(df: pd.DataFrame, direction: Direction) -> Tuple[bool, float]:
    """Price position relative to VWAP — above = bullish, below = bearish."""
    if len(df) < 5:
        return False, 0.0
    vwap_series = vwap(df)
    last_close = float(df["close"].iloc[-1])
    last_vwap = float(vwap_series.iloc[-1])
    if pd.isna(last_vwap):
        return False, 0.0
    
    dist_pct = abs(last_close - last_vwap) / last_vwap * 100 if last_vwap else 0
    # Extra score for being well above/below VWAP vs just barely above/below
    strength = min(1.0, dist_pct / 0.5)  # 0.5% distance = full credit
    
    if direction == Direction.LONG:
        passes = last_close > last_vwap
        score = strength if passes else 0.0
    else:
        passes = last_close < last_vwap
        score = strength if passes else 0.0
    return passes, score


def check_rsi(df: pd.DataFrame, direction: Direction,
              period: int = 14) -> Tuple[bool, float]:
    """RSI zone check: not overbought for LONG, not oversold for SHORT.
    Uses 35/65 thresholds (more reliable than 30/70 for intraday)."""
    if len(df) < period + 5:
        return False, 0.0
    rsi_series = rsi(df["close"], period)
    last_rsi = float(rsi_series.iloc[-1])
    if pd.isna(last_rsi):
        return False, 0.0
    
    if direction == Direction.LONG:
        # Best: RSI recovering from oversold (30-50 zone)
        if 30 <= last_rsi <= 50:
            return True, 1.0
        # Good: RSI in bullish momentum zone (50-65)
        if 50 < last_rsi <= 65:
            return True, 0.7
        # Risky: overbought
        if last_rsi > 70:
            return False, 0.0
        return True, 0.5
    else:  # SHORT
        if 50 <= last_rsi <= 70:
            return True, 1.0
        if 35 <= last_rsi < 50:
            return True, 0.7
        if last_rsi < 30:
            return False, 0.0
        return True, 0.5


def check_macd(df: pd.DataFrame, direction: Direction) -> Tuple[bool, float]:
    """MACD state: histogram positive and rising for LONG, negative and falling for SHORT."""
    if len(df) < 35:
        return False, 0.0
    _, _, hist = macd(df["close"])
    last_hist = float(hist.iloc[-1])
    prev_hist = float(hist.iloc[-2])
    if pd.isna(last_hist) or pd.isna(prev_hist):
        return False, 0.0
    
    # Fresh crossover gives best score
    fresh_cross_up = prev_hist <= 0 < last_hist
    fresh_cross_down = prev_hist >= 0 > last_hist
    trending_up = last_hist > 0 and last_hist > prev_hist
    trending_down = last_hist < 0 and last_hist < prev_hist
    
    if direction == Direction.LONG:
        if fresh_cross_up:
            return True, 1.0
        if trending_up:
            return True, 0.7
        if last_hist > 0:
            return True, 0.5
        return False, 0.0
    else:
        if fresh_cross_down:
            return True, 1.0
        if trending_down:
            return True, 0.7
        if last_hist < 0:
            return True, 0.5
        return False, 0.0


def check_adx(df: pd.DataFrame, min_adx: float = 20.0) -> Tuple[bool, float]:
    """ADX trend strength — stronger ADX = stronger trend confirmation."""
    if len(df) < 20:
        return False, 0.0
    adx_val, _, _ = adx(df)
    last_adx = float(adx_val.iloc[-1])
    if pd.isna(last_adx):
        return False, 0.0
    
    if last_adx >= 30:
        return True, 1.0   # strong trend
    if last_adx >= min_adx:
        return True, (last_adx - min_adx) / 10.0 + 0.5  # gradient 0.5-1.0
    return False, 0.0


def check_volume(df: pd.DataFrame, min_multiplier: float = 1.2) -> Tuple[bool, float]:
    """Volume confirmation: current bar volume above N× average."""
    if len(df) < 20:
        return False, 0.0
    
    if "volume" not in df.columns:
        return True, 0.5

    vol_avg = avg_volume(df, 20)
    last_vol = float(df["volume"].iloc[-1])
    last_avg = float(vol_avg.iloc[-1])
    
    if pd.isna(last_avg) or last_avg == 0:
        # If volume is 0 on average, it's likely an index without volume data.
        # Bypass the hard gate with a neutral score.
        return True, 0.5
    
    ratio = last_vol / last_avg
    if ratio >= 2.0:
        return True, 1.0   # very strong volume
    if ratio >= min_multiplier:
        return True, min(1.0, (ratio - min_multiplier) / 0.8 + 0.6)
    return False, 0.0


def check_price_action(df: pd.DataFrame, direction: Direction) -> Tuple[bool, float]:
    """Candle pattern confirmation aligned with trade direction."""
    patterns = candle_pattern(df)
    if not patterns:
        return False, 0.0
    
    if direction == Direction.LONG:
        bullish_patterns = ["hammer", "bullish_engulfing", "strong_bullish", "pin_bar_bull"]
        for p in bullish_patterns:
            if patterns.get(p, False):
                return True, 1.0
        # Doji at support is neutral-to-bullish
        if patterns.get("doji", False):
            return True, 0.5
    else:
        bearish_patterns = ["shooting_star", "bearish_engulfing", "strong_bearish", "pin_bar_bear"]
        for p in bearish_patterns:
            if patterns.get(p, False):
                return True, 1.0
        if patterns.get("doji", False):
            return True, 0.5
    return False, 0.0


def check_market_structure(df: pd.DataFrame, direction: Direction,
                            lookback: int = 20) -> Tuple[bool, float]:
    """Market structure: higher highs/higher lows for LONG, lower highs/lower lows for SHORT."""
    if len(df) < lookback + 5:
        return False, 0.0
    
    closes = df["close"].iloc[-lookback:].values
    highs = df["high"].iloc[-lookback:].values
    lows = df["low"].iloc[-lookback:].values
    
    # Simple linear trend of closes
    x = np.arange(len(closes))
    slope = np.polyfit(x, closes, 1)[0]
    
    # Higher highs check (recent high > mid-period high)
    mid = lookback // 2
    recent_high = max(highs[mid:])
    prior_high = max(highs[:mid])
    recent_low = min(lows[mid:])
    prior_low = min(lows[:mid])
    
    if direction == Direction.LONG:
        hh = recent_high > prior_high
        hl = recent_low > prior_low
        slope_ok = slope > 0
        passes = slope_ok and (hh or hl)
        score = 1.0 if (hh and hl and slope_ok) else (0.7 if passes else 0.0)
    else:
        ll = recent_low < prior_low
        lh = recent_high < prior_high
        slope_ok = slope < 0
        passes = slope_ok and (ll or lh)
        score = 1.0 if (ll and lh and slope_ok) else (0.7 if passes else 0.0)
    return passes, score


def check_htf_alignment(htf_df: pd.DataFrame, direction: Direction) -> Tuple[bool, float]:
    """Higher timeframe EMA trend alignment. True if HTF EMA trend matches direction."""
    if htf_df is None or len(htf_df) < 25:
        return True, 0.5  # neutral — can't determine, don't block
    ema_fast = ema(htf_df["close"], 9)
    ema_slow = ema(htf_df["close"], 21)
    last_fast = float(ema_fast.iloc[-1])
    last_slow = float(ema_slow.iloc[-1])
    
    if direction == Direction.LONG:
        passes = last_fast > last_slow
    else:
        passes = last_fast < last_slow
    return passes, 1.0 if passes else 0.0


def check_order_block(df: pd.DataFrame, direction: Direction) -> Tuple[bool, float]:
    """ICT Order Block: check if current price is in a bullish/bearish order block zone."""
    from core.indicators import detect_order_blocks
    blocks = detect_order_blocks(df)
    last_close = float(df["close"].iloc[-1])
    
    for block in reversed(blocks):  # most recent first
        in_zone = block["low"] <= last_close <= block["high"]
        if in_zone:
            if direction == Direction.LONG and block["type"] == "BULLISH_OB":
                return True, 1.0
            if direction == Direction.SHORT and block["type"] == "BEARISH_OB":
                return True, 1.0
    return False, 0.0


def check_fvg(df: pd.DataFrame, direction: Direction) -> Tuple[bool, float]:
    """ICT Fair Value Gap: check if price is near or within an unfilled FVG."""
    from core.indicators import detect_fair_value_gaps
    fvgs = detect_fair_value_gaps(df)
    last_close = float(df["close"].iloc[-1])
    
    for fvg in reversed(fvgs):
        in_zone = fvg["low"] <= last_close <= fvg["high"]
        if in_zone:
            if direction == Direction.LONG and fvg["type"] == "BULLISH_FVG":
                return True, 1.0
            if direction == Direction.SHORT and fvg["type"] == "BEARISH_FVG":
                return True, 1.0
    return False, 0.0
