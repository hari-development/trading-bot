import numpy as np
import pandas as pd
import pytest

from core.indicators import ema, rsi, macd, atr, adx, bollinger_bands, vwap, supertrend, avg_volume


def make_df(n=100, seed=42):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    high = close + rng.uniform(0.5, 1.5, n)
    low = close - rng.uniform(0.5, 1.5, n)
    open_ = close + rng.normal(0, 0.5, n)
    volume = rng.integers(50_000, 500_000, n)
    idx = pd.date_range("2026-01-01 09:15", periods=n, freq="5min")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)


def test_ema_length_matches_input():
    df = make_df()
    result = ema(df["close"], 21)
    assert len(result) == len(df)


def test_rsi_bounded_0_100():
    df = make_df()
    result = rsi(df["close"], 14).dropna()
    assert (result >= 0).all() and (result <= 100).all()


def test_macd_returns_three_series():
    df = make_df()
    macd_line, signal_line, hist = macd(df["close"])
    assert len(macd_line) == len(signal_line) == len(hist) == len(df)


def test_atr_non_negative():
    df = make_df()
    result = atr(df, 14).dropna()
    assert (result >= 0).all()


def test_adx_bounded_0_100():
    df = make_df()
    adx_val, plus_di, minus_di = adx(df, 14)
    adx_val = adx_val.dropna()
    assert (adx_val >= 0).all() and (adx_val <= 100).all()


def test_bollinger_upper_above_lower():
    df = make_df()
    upper, mid, lower = bollinger_bands(df["close"], 20, 2.0)
    valid = upper.dropna().index.intersection(lower.dropna().index)
    assert (upper[valid] >= lower[valid]).all()


def test_vwap_within_reasonable_range():
    df = make_df()
    result = vwap(df).dropna()
    assert (result > 0).all()


def test_supertrend_returns_trend_series():
    df = make_df()
    line, trend = supertrend(df, 10, 3.0)
    assert set(trend.unique()).issubset({1, -1})


def test_avg_volume_positive():
    df = make_df()
    result = avg_volume(df, 20).dropna()
    assert (result > 0).all()
