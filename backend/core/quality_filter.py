"""
Trade Quality Filter.

This is the gate every Signal must pass through before the risk manager
even sees it. Reasons for rejection are always logged so the "why did the
bot skip that setup" question is always answerable from logs.

Gate order (fail-fast):
  1. News / macro-event blackout
  2. Trading window (time-of-day)
  3. Minimum confirmations
  4. Risk-reward sanity
  5. ATR% (volatility blowout)
  6. Liquidity / volume (stocks only)
  7. Win probability threshold
  8. Regime suitability
"""
from dataclasses import dataclass
from datetime import datetime, time
from typing import Tuple

import pandas as pd

from config.settings import quality_config
from core.indicators import avg_volume, atr
from core.models import Signal
from core.news_filter import is_news_blackout
from core.regime import Regime, RegimeSnapshot


@dataclass
class FilterResult:
    passed: bool
    reason: str = ""


def _within_trading_window(now: datetime) -> Tuple[bool, str]:
    open_t = time(9, 15)
    close_t = time(15, 30)
    avoid_open_until = (
        datetime.combine(now.date(), open_t)
        + pd.Timedelta(minutes=quality_config.avoid_first_minutes_after_open)
    ).time()
    avoid_close_after = (
        datetime.combine(now.date(), close_t)
        - pd.Timedelta(minutes=quality_config.avoid_last_minutes_before_close)
    ).time()
    t = now.time()
    if t < open_t or t > close_t:
        return False, "outside_market_hours"
    if t < avoid_open_until:
        return False, "within_opening_whipsaw_window"
    if t > avoid_close_after:
        return False, "within_closing_volatility_window"
    return True, ""


def evaluate_signal_quality(
    signal: Signal, df: pd.DataFrame, regime: RegimeSnapshot
) -> FilterResult:
    # 1. News blackout — highest priority gate
    if quality_config.enable_news_filter:
        blackout, event_label = is_news_blackout(signal.timestamp)
        if blackout:
            return FilterResult(False, f"news_blackout({event_label})")

    # 2. Trading window
    ok, reason = _within_trading_window(signal.timestamp)
    if not ok:
        return FilterResult(False, reason)

    # 3. Confirmation count
    if len(signal.confirmations) < quality_config.min_confirmations:
        return FilterResult(False, f"insufficient_confirmations({len(signal.confirmations)})")

    # 4. Risk-reward sanity
    if signal.risk_reward_ratio < 1e-9:
        return FilterResult(False, "invalid_risk_reward")
    if signal.risk_reward_ratio < 1.0:
        return FilterResult(False, f"risk_reward_too_low({signal.risk_reward_ratio:.2f})")

    # 5. Volatility blowout check
    last_atr = atr(df, 14).iloc[-1]
    last_close = df["close"].iloc[-1]
    if last_close:
        atr_pct = (last_atr / last_close) * 100
        if atr_pct > quality_config.max_atr_pct_of_price:
            return FilterResult(False, f"volatility_too_high(atr_pct={atr_pct:.2f})")

    # 6. Liquidity check (skip for indices — they don't report meaningful volume)
    is_index = (
        signal.symbol in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX")
        or "NIFTY" in signal.symbol
        or "SENSEX" in signal.symbol
    )
    if not is_index:
        vol_avg = avg_volume(df, 20).iloc[-1]
        if pd.isna(vol_avg) or vol_avg < quality_config.min_avg_volume:
            return FilterResult(False, f"liquidity_too_low(avg_vol={vol_avg:.0f})")

    # 7. Win probability threshold
    if signal.win_probability < quality_config.min_win_probability:
        return FilterResult(
            False, f"win_probability_below_threshold({signal.win_probability:.2f})"
        )

    # 8. Regime suitability
    if regime.regime == Regime.HIGH_VOLATILITY:
        return FilterResult(False, "regime_high_volatility_lockout")

    # Trend-following strategies must not fire against a strong opposing trend
    trend_following = {"ema_supertrend", "vwap_breakout", "opening_range_breakout"}
    if signal.strategy_name in trend_following:
        if regime.regime == Regime.TRENDING_DOWN and signal.direction.value == "LONG":
            return FilterResult(False, "signal_against_dominant_downtrend")
        if regime.regime == Regime.TRENDING_UP and signal.direction.value == "SHORT":
            return FilterResult(False, "signal_against_dominant_uptrend")

    return FilterResult(True, "all_checks_passed")
