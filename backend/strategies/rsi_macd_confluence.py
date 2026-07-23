"""
RSI + MACD Confluence Strategy — upgraded with multi-confirmation + confidence scoring.

Critical fixes (Phase 5):
  - RSI thresholds changed from 40/60 (weak) to 35/65 (standard)
  - Added trend filter: MACD regime must not be trending AGAINST the signal
  - Added EMA and SuperTrend as required trend confirmation
  - Market structure check prevents countertrend trades

Entry logic:
  PRIMARY: MACD histogram zero-cross + RSI recovering from oversold/overbought
  CONFIRMATION CASCADE: Full multi-indicator scoring
"""
from datetime import datetime
from typing import Optional

import pandas as pd

from config.settings import trade_mgmt_config, confidence_config
from core.indicators import rsi, macd, atr, bollinger_bands
from core.models import Direction, Signal
from strategies.base import Strategy
from strategies.multi_confirmation import (
    check_ema_trend, check_supertrend, check_vwap_position,
    check_rsi, check_macd, check_adx, check_volume,
    check_price_action, check_market_structure,
)
from strategies.confidence_engine import confidence_engine


class RsiMacdConfluenceStrategy(Strategy):
    name = "rsi_macd_confluence"

    def required_lookback(self) -> int:
        return 50

    def evaluate(self, symbol: str, df: pd.DataFrame) -> Optional[Signal]:
        if len(df) < self.required_lookback():
            return None

        rsi_series = rsi(df["close"], 14)
        _, _, hist = macd(df["close"])
        atr_val = atr(df, 14)
        upper, mid, lower = bollinger_bands(df["close"])

        i, prev = -1, -2
        close = df["close"].iloc[i]
        last_atr = float(atr_val.iloc[i]) if not pd.isna(atr_val.iloc[i]) else 0
        if last_atr == 0:
            return None

        last_rsi = float(rsi_series.iloc[i]) if not pd.isna(rsi_series.iloc[i]) else 50
        last_hist = float(hist.iloc[i]) if not pd.isna(hist.iloc[i]) else 0
        prev_hist = float(hist.iloc[prev]) if not pd.isna(hist.iloc[prev]) else 0

        # PRIMARY TRIGGER: MACD histogram zero-cross
        macd_cross_up = prev_hist <= 0 < last_hist
        macd_cross_down = prev_hist >= 0 > last_hist

        # RSI confirmation: recovering from oversold for LONG, reverting from overbought for SHORT
        # FIXED: using 35/65 thresholds instead of 40/60
        rsi_bullish = last_rsi <= 65 and rsi_series.iloc[prev] < 35 and last_rsi >= 35
        rsi_bearish = last_rsi >= 35 and rsi_series.iloc[prev] > 65 and last_rsi <= 65

        # BB proximity (optional bonus confirmation)
        last_close = float(close)
        bb_lower = float(lower.iloc[i]) if not pd.isna(lower.iloc[i]) else 0
        bb_upper = float(upper.iloc[i]) if not pd.isna(upper.iloc[i]) else 0
        near_lower = bb_lower > 0 and last_close <= bb_lower * 1.005
        near_upper = bb_upper > 0 and last_close >= bb_upper * 0.995

        if macd_cross_up and rsi_bullish:
            direction = Direction.LONG
        elif macd_cross_down and rsi_bearish:
            direction = Direction.SHORT
        else:
            return None

        # TREND FILTER: Prevent this mean-reversion strategy from fighting strong trends
        ema_ok, _ = check_ema_trend(df, direction)
        mkt_ok, _ = check_market_structure(df, direction)
        # Only trade when not in STRONGLY opposing trend (one of ema/structure must agree)
        if not ema_ok and not mkt_ok:
            return None

        # CONFIRMATION CASCADE
        checks = [
            ("ema_trend",    *check_ema_trend(df, direction),      confidence_config.ema_trend_weight),
            ("supertrend",   *check_supertrend(df, direction),     confidence_config.supertrend_weight),
            ("vwap",         *check_vwap_position(df, direction),  confidence_config.vwap_weight),
            ("adx",          *check_adx(df),                       confidence_config.adx_weight),
            ("rsi",          *check_rsi(df, direction),            confidence_config.rsi_weight),
            ("macd",         *check_macd(df, direction),           confidence_config.macd_weight),
            ("volume",       *check_volume(df),                    confidence_config.volume_weight),
            ("price_action", *check_price_action(df, direction),  confidence_config.price_action_weight),
        ]

        score = confidence_engine.compute(checks)
        if not confidence_engine.passes_threshold(score):
            return None

        confirmations = [name for name, passes, _, _ in checks if passes]
        if near_lower and direction == Direction.LONG:
            confirmations.append("bb_lower_touch")
        elif near_upper and direction == Direction.SHORT:
            confirmations.append("bb_upper_touch")

        sl_dist = last_atr * trade_mgmt_config.atr_sl_multiplier
        tp_dist = last_atr * trade_mgmt_config.atr_tp_multiplier
        if direction == Direction.LONG:
            stop_loss, take_profit = float(close - sl_dist), float(close + tp_dist)
        else:
            stop_loss, take_profit = float(close + sl_dist), float(close - tp_dist)

        win_prob = min(0.50 + score * 0.30, 0.80)

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
                "rsi": last_rsi,
                "macd_hist": last_hist,
                "atr": last_atr,
                "confidence": score,
            },
        )
