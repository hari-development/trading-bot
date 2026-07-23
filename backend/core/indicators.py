"""
Technical indicator library. Pure functions operating on pandas DataFrames
with columns: open, high, low, close, volume.
No external TA-lib dependency required — implemented with pandas/numpy so
the bot runs anywhere without native binary installs.
"""
import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period).mean()


def adx(df: pd.DataFrame, period: int = 14):
    high, low, close = df["high"], df["low"], df["close"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr = atr(df, period) * period  # de-smoothed true range approximation
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, min_periods=period).mean() / tr.replace(0, np.nan)
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, min_periods=period).mean() / tr.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_val = dx.ewm(alpha=1 / period, min_periods=period).mean()
    return adx_val, plus_di, minus_di


def bollinger_bands(series: pd.Series, period: int = 20, std_mult: float = 2.0):
    mid = series.rolling(period).mean()
    std = series.rolling(period).std()
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    return upper, mid, lower


def vwap(df: pd.DataFrame) -> pd.Series:
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    cum_vol = df["volume"].cumsum()
    cum_tp_vol = (typical_price * df["volume"]).cumsum()
    return cum_tp_vol / cum_vol.replace(0, np.nan)


def supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0):
    """
    SuperTrend indicator — fully vectorised, pandas 2.x safe.
    Returns (supertrend_line, trend) where trend==1 is bullish, -1 is bearish.
    """
    hl2 = (df["high"] + df["low"]) / 2
    atr_val = atr(df, period)
    basic_upper = hl2 + multiplier * atr_val
    basic_lower = hl2 - multiplier * atr_val

    close = df["close"].values
    upper = basic_upper.values.copy()
    lower = basic_lower.values.copy()
    trend = np.ones(len(df), dtype=np.int64)

    for i in range(1, len(df)):
        # Final upper band: carry forward if previous close was below prior upper
        upper[i] = basic_upper.values[i] if close[i - 1] > upper[i - 1] \
            else min(basic_upper.values[i], upper[i - 1])
        # Final lower band: carry forward if previous close was above prior lower
        lower[i] = basic_lower.values[i] if close[i - 1] < lower[i - 1] \
            else max(basic_lower.values[i], lower[i - 1])
        # Trend direction
        if trend[i - 1] == 1:
            trend[i] = -1 if close[i] < lower[i] else 1
        else:
            trend[i] = 1 if close[i] > upper[i] else -1

    supertrend_line = np.where(trend == 1, lower, upper)
    return (
        pd.Series(supertrend_line, index=df.index, name="supertrend"),
        pd.Series(trend, index=df.index, name="trend"),
    )


def opening_range(df: pd.DataFrame, minutes: int = 15):
    """Returns (high, low) of the first `minutes` of the session for a
    single-day intraday dataframe indexed by datetime."""
    if df.empty:
        return None, None
    session_start = df.index[0]
    window_end = session_start + pd.Timedelta(minutes=minutes)
    window = df[df.index <= window_end]
    return window["high"].max(), window["low"].min()


def avg_volume(df: pd.DataFrame, period: int = 20) -> pd.Series:
    return df["volume"].rolling(period).mean()


# ---------------------------------------------------------------------------
# Phase 3: Enhanced Indicator Library
# ---------------------------------------------------------------------------

def stochastic_rsi(series: pd.Series, rsi_period: int = 14, stoch_period: int = 14) -> pd.Series:
    """Stochastic RSI — normalizes RSI into a 0-100 range oscillator.
    More sensitive than plain RSI for identifying momentum shifts."""
    rsi_values = rsi(series, rsi_period)
    rsi_min = rsi_values.rolling(stoch_period).min()
    rsi_max = rsi_values.rolling(stoch_period).max()
    denom = (rsi_max - rsi_min).replace(0, np.nan)
    return (rsi_values - rsi_min) / denom * 100


def williams_r(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Williams %R — overbought/oversold oscillator.
    Values: -80 to -100 = oversold (bullish), -0 to -20 = overbought (bearish)."""
    highest_high = df["high"].rolling(period).max()
    lowest_low = df["low"].rolling(period).min()
    denom = (highest_high - lowest_low).replace(0, np.nan)
    return (highest_high - df["close"]) / denom * -100


def obv(df: pd.DataFrame) -> pd.Series:
    """On-Balance Volume — cumulative volume indicator for trend confirmation.
    Rising OBV = accumulation (bullish); Falling OBV = distribution (bearish)."""
    direction = np.sign(df["close"].diff())
    return (direction * df["volume"]).fillna(0).cumsum()


def heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """Heikin-Ashi candles — smoothed price bars that filter market noise.
    Returns DataFrame with columns: ha_open, ha_high, ha_low, ha_close."""
    ha = pd.DataFrame(index=df.index)
    ha["ha_close"] = (df["open"] + df["high"] + df["low"] + df["close"]) / 4
    ha_open = [(df["open"].iloc[0] + df["close"].iloc[0]) / 2]
    for i in range(1, len(df)):
        ha_open.append((ha_open[i - 1] + ha["ha_close"].iloc[i - 1]) / 2)
    ha["ha_open"] = ha_open
    ha["ha_high"] = pd.concat([df["high"], ha["ha_open"], ha["ha_close"]], axis=1).max(axis=1)
    ha["ha_low"] = pd.concat([df["low"], ha["ha_open"], ha["ha_close"]], axis=1).min(axis=1)
    return ha


def detect_swing_levels(df: pd.DataFrame, lookback: int = 5) -> dict:
    """Detects recent swing highs and lows as support/resistance levels.
    Returns dict with 'swing_highs' and 'swing_lows' as sorted lists of prices."""
    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    swing_highs = []
    swing_lows = []
    for i in range(lookback, len(df) - lookback):
        if highs[i] == max(highs[i - lookback:i + lookback + 1]):
            swing_highs.append(float(highs[i]))
        if lows[i] == min(lows[i - lookback:i + lookback + 1]):
            swing_lows.append(float(lows[i]))
    return {
        "swing_highs": sorted(set(swing_highs), reverse=True)[:5],
        "swing_lows": sorted(set(swing_lows))[:5],
    }


def detect_order_blocks(df: pd.DataFrame, lookback: int = 10) -> list:
    """ICT Order Block detection — finds institutional supply/demand zones.
    An order block is the last up/down candle before a significant move.
    Returns list of dicts: {type, high, low, bar_idx}."""
    blocks = []
    if len(df) < lookback + 3:
        return blocks
    for i in range(lookback, len(df) - 2):
        # Bullish order block: last bearish candle before a strong bullish impulse
        if (df["close"].iloc[i] < df["open"].iloc[i]  # current bar is bearish
                and df["close"].iloc[i + 1] > df["open"].iloc[i + 1]   # next bar bullish
                and df["close"].iloc[i + 1] > df["high"].iloc[i]):      # strong bullish break
            blocks.append({
                "type": "BULLISH_OB",
                "high": float(df["high"].iloc[i]),
                "low": float(df["low"].iloc[i]),
                "bar_idx": i,
            })
        # Bearish order block: last bullish candle before a strong bearish impulse
        elif (df["close"].iloc[i] > df["open"].iloc[i]
              and df["close"].iloc[i + 1] < df["open"].iloc[i + 1]
              and df["close"].iloc[i + 1] < df["low"].iloc[i]):
            blocks.append({
                "type": "BEARISH_OB",
                "high": float(df["high"].iloc[i]),
                "low": float(df["low"].iloc[i]),
                "bar_idx": i,
            })
    return blocks[-5:]  # return most recent 5


def detect_fair_value_gaps(df: pd.DataFrame) -> list:
    """ICT Fair Value Gap (FVG) detection — identifies price imbalance zones.
    Bullish FVG: candle[i-1].high < candle[i+1].low (gap between candles i-1 and i+1)
    Bearish FVG: candle[i-1].low > candle[i+1].high
    Returns list of dicts: {type, high, low, bar_idx}."""
    fvgs = []
    if len(df) < 3:
        return fvgs
    for i in range(1, len(df) - 1):
        prev_high = df["high"].iloc[i - 1]
        prev_low = df["low"].iloc[i - 1]
        next_high = df["high"].iloc[i + 1]
        next_low = df["low"].iloc[i + 1]
        if prev_high < next_low:   # bullish FVG
            fvgs.append({
                "type": "BULLISH_FVG",
                "high": float(next_low),
                "low": float(prev_high),
                "bar_idx": i,
            })
        elif prev_low > next_high:  # bearish FVG
            fvgs.append({
                "type": "BEARISH_FVG",
                "high": float(prev_low),
                "low": float(next_high),
                "bar_idx": i,
            })
    return fvgs[-5:]  # most recent 5


def candle_pattern(df: pd.DataFrame) -> dict:
    """Detects key price action candle patterns on the last 2 bars.
    Returns dict of detected patterns with True/False values."""
    if len(df) < 2:
        return {}
    i = -1
    prev = -2
    o, h, l, c = df["open"].iloc[i], df["high"].iloc[i], df["low"].iloc[i], df["close"].iloc[i]
    p_o, p_h, p_l, p_c = df["open"].iloc[prev], df["high"].iloc[prev], df["low"].iloc[prev], df["close"].iloc[prev]
    body = abs(c - o)
    candle_range = h - l
    prev_body = abs(p_c - p_o)
    prev_range = p_h - p_l

    is_bullish = c > o
    is_bearish = c < o
    body_ratio = body / candle_range if candle_range > 0 else 0
    upper_wick = (h - max(o, c)) / candle_range if candle_range > 0 else 0
    lower_wick = (min(o, c) - l) / candle_range if candle_range > 0 else 0

    return {
        "hammer": lower_wick >= 0.6 and body_ratio <= 0.3 and upper_wick <= 0.1,
        "shooting_star": upper_wick >= 0.6 and body_ratio <= 0.3 and lower_wick <= 0.1,
        "bullish_engulfing": is_bullish and p_c < p_o and c > p_o and o < p_c,
        "bearish_engulfing": is_bearish and p_c > p_o and c < p_o and o > p_c,
        "doji": body_ratio <= 0.1,
        "strong_bullish": is_bullish and body_ratio >= 0.7,
        "strong_bearish": is_bearish and body_ratio >= 0.7,
        "pin_bar_bull": lower_wick >= 0.5 and body_ratio <= 0.4,
        "pin_bar_bear": upper_wick >= 0.5 and body_ratio <= 0.4,
    }
