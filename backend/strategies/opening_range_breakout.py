"""
Opening Range Breakout (ORB) Strategy — upgraded with multi-confirmation + confidence scoring.

Fixes (Phase 5):
  - Added gap check (ORB works best on gap-open days)
  - Added range-size validation relative to ATR
  - Added failed breakout guard (prevent re-entry on false breaks)
  - Added confidence scoring cascade

Entry logic:
  PRIMARY: First N-minute range breakout above ORH or below ORL
  CONFIRMATION CASCADE: Full multi-indicator scoring
"""
from datetime import datetime, time as dtime
from typing import Optional

import pandas as pd

from config.settings import trade_mgmt_config, confidence_config
from core.indicators import atr, avg_volume, opening_range
from core.models import Direction, Signal
from strategies.base import Strategy
from strategies.multi_confirmation import (
    check_ema_trend, check_supertrend, check_vwap_position,
    check_rsi, check_macd, check_adx, check_volume, check_price_action,
)
from strategies.confidence_engine import confidence_engine


class OpeningRangeBreakoutStrategy(Strategy):
    name = "opening_range_breakout"

    def __init__(self, range_minutes: int = 15):
        self.range_minutes = range_minutes
        # Track symbols that have already triggered today to prevent re-entry
        self._triggered_today: dict = {}  # symbol -> date

    def required_lookback(self) -> int:
        return 50

    def evaluate(self, symbol: str, df: pd.DataFrame) -> Optional[Signal]:
        if len(df) < self.required_lookback():
            return None

        atr_val = atr(df, 14)
        last_atr = float(atr_val.iloc[-1]) if not pd.isna(atr_val.iloc[-1]) else 0
        if last_atr == 0:
            return None

        # Only look for ORB after the range window closes
        if isinstance(df.index, pd.DatetimeIndex):
            now = df.index[-1]
            today = now.date()
            today_bars = df[df.index.date == today]

            # Only fire once per symbol per day
            if self._triggered_today.get(symbol) == today:
                return None

            if today_bars.empty:
                return None

            # Check we are past the range window
            range_open_time = dtime(9, 15)
            range_close_time = dtime(9, 15 + self.range_minutes)
            current_time = now.time()
            if current_time <= range_close_time:
                return None  # range window not yet closed

            # Compute ORH / ORL
            or_high, or_low = opening_range(today_bars, self.range_minutes)
            if or_high is None or or_low is None:
                return None
            range_size = or_high - or_low

            # Range size validation: reject if range is too narrow (noise) or
            # too wide relative to ATR (news-driven chaos)
            if range_size <= 0 or range_size < last_atr * 0.3 or range_size > last_atr * 4:
                return None
        else:
            return None  # ORB requires datetime index

        close = float(df["close"].iloc[-1])
        prev_close = float(df["close"].iloc[-2])

        # GAP CHECK: prefer ORB on gap-open days (more reliable)
        # Prior session close = last bar of prior day
        prior_bars = df[df.index.date < today]
        gap_pct = 0.0
        if not prior_bars.empty:
            prior_close = float(prior_bars["close"].iloc[-1])
            first_open = float(today_bars["open"].iloc[0])
            gap_pct = abs(first_open - prior_close) / prior_close * 100 if prior_close else 0.0

        # TRIGGER: Price breaking ORH or ORL (single bar only, prevent re-fires)
        breakout_long = prev_close <= or_high < close
        breakout_short = prev_close >= or_low > close

        if breakout_long:
            direction = Direction.LONG
        elif breakout_short:
            direction = Direction.SHORT
        else:
            return None

        # FALSE BREAKOUT GUARD: if the close is immediately back inside the range, skip
        if direction == Direction.LONG and close < or_high * 1.001:
            return None
        if direction == Direction.SHORT and close > or_low * 0.999:
            return None

        # VOLUME CONFIRMATION (hard gate for ORB)
        vol_passes, vol_score = check_volume(df, min_multiplier=1.2)
        if not vol_passes:
            return None

        # CONFIRMATION CASCADE
        checks = [
            ("ema_trend",    *check_ema_trend(df, direction),      confidence_config.ema_trend_weight),
            ("supertrend",   *check_supertrend(df, direction),     confidence_config.supertrend_weight),
            ("vwap",         *check_vwap_position(df, direction),  confidence_config.vwap_weight),
            ("adx",          *check_adx(df),                       confidence_config.adx_weight),
            ("rsi",          *check_rsi(df, direction),            confidence_config.rsi_weight),
            ("macd",         *check_macd(df, direction),           confidence_config.macd_weight),
            ("volume",       vol_passes, vol_score,                confidence_config.volume_weight),
            ("price_action", *check_price_action(df, direction),  confidence_config.price_action_weight),
        ]

        # Boost confidence on gap days (ORB more reliable with gap)
        score = confidence_engine.compute(checks)
        if gap_pct >= 0.3:
            score = min(1.0, score + 0.05)  # slight boost for gap confirmation

        if not confidence_engine.passes_threshold(score):
            return None

        # Mark as triggered for today to prevent re-entry
        self._triggered_today[symbol] = today
        confirmations = [name for name, passes, _, _ in checks if passes]
        if gap_pct >= 0.3:
            confirmations.append(f"gap_confirmed_{gap_pct:.1f}pct")

        # Adaptive SL: wider of ATR-based or range-based
        sl_dist = max(last_atr * trade_mgmt_config.atr_sl_multiplier, range_size * 0.3)
        tp_dist = last_atr * trade_mgmt_config.atr_tp_multiplier
        if direction == Direction.LONG:
            stop_loss, take_profit = float(close - sl_dist), float(close + tp_dist)
        else:
            stop_loss, take_profit = float(close + sl_dist), float(close - tp_dist)

        win_prob = min(0.50 + score * 0.30 + (0.05 if gap_pct >= 0.5 else 0), 0.80)

        return Signal(
            symbol=symbol,
            timestamp=datetime.now(),
            direction=direction,
            strategy_name=self.name,
            entry_price=float(close),
            stop_loss=stop_loss,
            take_profit=take_profit,
            confirmations=confirmations,
            win_probability=win_prob,
            confidence_score=score,
            indicator_snapshot={
                "or_high": float(or_high),
                "or_low": float(or_low),
                "range_size": float(range_size),
                "gap_pct": round(gap_pct, 2),
                "atr": last_atr,
                "confidence": score,
            },
        )
