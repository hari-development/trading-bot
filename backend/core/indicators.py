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
