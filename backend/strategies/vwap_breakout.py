"""
VWAP Breakout Strategy — upgraded with multi-confirmation + confidence scoring.

Entry logic (Phase 5):
  PRIMARY: Price crosses VWAP with volume spike
  CONFIRMATION CASCADE:
    - VWAP position (primary)
    - Volume strength
    - EMA trend alignment (HTF bias)
    - SuperTrend confirmation
    - RSI zone
    - MACD state
    - Price action (candle body)
    - Market structure

Only signals with confidence >= threshold are returned.
"""
from datetime import datetime
from typing import Optional

import pandas as pd

from config.settings import trade_mgmt_config, confidence_config
from core.indicators import vwap, atr, avg_volume, ema, supertrend
from core.models import Direction, Signal
from strategies.base import Strategy
from strategies.multi_confirmation import (
    check_ema_trend, check_supertrend, check_vwap_position,
    check_rsi, check_macd, check_volume, check_price_action, check_market_structure,
)
from strategies.confidence_engine import confidence_engine


class VwapBreakoutStrategy(Strategy):
    name = "vwap_breakout"

    def required_lookback(self) -> int:
        return 40

    def evaluate(self, symbol: str, df: pd.DataFrame) -> Optional[Signal]:
        if len(df) < self.required_lookback():
            return None

        vwap_series = vwap(df)
        atr_val = atr(df, 14)

        i, prev = -1, -2
        close = df["close"].iloc[i]
        last_atr = atr_val.iloc[i]
        if pd.isna(last_atr) or last_atr == 0 or pd.isna(vwap_series.iloc[i]):
            return None

        # PRIMARY TRIGGER: price crossing VWAP with volume spike
        crossed_above = df["close"].iloc[prev] <= vwap_series.iloc[prev] and close > vwap_series.iloc[i]
        crossed_below = df["close"].iloc[prev] >= vwap_series.iloc[prev] and close < vwap_series.iloc[i]

        if crossed_above:
            direction = Direction.LONG
        elif crossed_below:
            direction = Direction.SHORT
        else:
            return None

        # Volume must spike — no volume = VWAP cross is likely noise
        vol_passes, vol_score = check_volume(df, min_multiplier=1.3)
        if not vol_passes:
            return None  # hard gate: VWAP breakout requires volume

        # CONFIRMATION CASCADE
        checks = [
            ("ema_trend",    *check_ema_trend(df, direction),       confidence_config.ema_trend_weight),
            ("supertrend",   *check_supertrend(df, direction),      confidence_config.supertrend_weight),
            ("vwap",         *check_vwap_position(df, direction),   confidence_config.vwap_weight),
            ("adx",          True, min(1.0, vol_score),             confidence_config.adx_weight),
            ("rsi",          *check_rsi(df, direction),             confidence_config.rsi_weight),
            ("macd",         *check_macd(df, direction),            confidence_config.macd_weight),
            ("volume",       vol_passes, vol_score,                 confidence_config.volume_weight),
            ("price_action", *check_price_action(df, direction),   confidence_config.price_action_weight),
        ]

        score = confidence_engine.compute(checks)
        if not confidence_engine.passes_threshold(score):
            return None

        confirmations = [name for name, passes, _, _ in checks if passes]

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
                "vwap": float(vwap_series.iloc[i]),
                "atr": float(last_atr),
                "confidence": score,
            },
        )
