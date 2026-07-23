"""
EMA SuperTrend Strategy — upgraded with multi-confirmation + confidence scoring.

Entry logic (Phase 5):
  PRIMARY: EMA 9/21 crossover confirmed by SuperTrend direction
  CONFIRMATION CASCADE (all scored by WeightedConfidenceEngine):
    - EMA trend alignment
    - SuperTrend direction
    - VWAP position
    - ADX trend strength
    - RSI zone
    - MACD state
    - Volume spike
    - Price action (candle pattern)
    - Market structure (HH/HL or LH/LL)

Only signals with confidence >= config threshold are returned.
"""
from datetime import datetime
from typing import Optional

import pandas as pd

from config.settings import trade_mgmt_config, confidence_config
from core.indicators import ema, supertrend, atr, adx, avg_volume
from core.models import Direction, Signal
from strategies.base import Strategy
from strategies.multi_confirmation import (
    check_ema_trend, check_supertrend, check_vwap_position,
    check_rsi, check_macd, check_adx, check_volume,
    check_price_action, check_market_structure,
)
from strategies.confidence_engine import confidence_engine


class EmaSupertrendStrategy(Strategy):
    name = "ema_supertrend"

    def __init__(self, fast: int = 9, slow: int = 21, st_period: int = 10, st_mult: float = 3.0):
        self.fast = fast
        self.slow = slow
        self.st_period = st_period
        self.st_mult = st_mult

    def required_lookback(self) -> int:
        return max(self.slow, self.st_period) + 30

    def evaluate(self, symbol: str, df: pd.DataFrame) -> Optional[Signal]:
        if len(df) < self.required_lookback():
            return None

        ema_fast = ema(df["close"], self.fast)
        ema_slow = ema(df["close"], self.slow)
        st_line, st_trend = supertrend(df, self.st_period, self.st_mult)
        atr_val = atr(df, 14)

        i = -1
        prev = -2
        close = df["close"].iloc[i]
        last_atr = atr_val.iloc[i]
        if pd.isna(last_atr) or last_atr == 0:
            return None

        # PRIMARY TRIGGER: EMA crossover + SuperTrend alignment
        bullish_cross = ema_fast.iloc[prev] <= ema_slow.iloc[prev] and ema_fast.iloc[i] > ema_slow.iloc[i]
        bearish_cross = ema_fast.iloc[prev] >= ema_slow.iloc[prev] and ema_fast.iloc[i] < ema_slow.iloc[i]
        st_bullish = st_trend.iloc[i] == 1
        st_bearish = st_trend.iloc[i] == -1

        # Determine direction from primary trigger
        if bullish_cross and st_bullish:
            direction = Direction.LONG
        elif bearish_cross and st_bearish:
            direction = Direction.SHORT
        else:
            return None

        # CONFIRMATION CASCADE — compute confidence score
        checks = [
            ("ema_trend",        *check_ema_trend(df, direction, self.fast, self.slow),       confidence_config.ema_trend_weight),
            ("supertrend",       *check_supertrend(df, direction, self.st_period, self.st_mult), confidence_config.supertrend_weight),
            ("vwap",             *check_vwap_position(df, direction),                          confidence_config.vwap_weight),
            ("adx",              *check_adx(df),                                               confidence_config.adx_weight),
            ("rsi",              *check_rsi(df, direction),                                    confidence_config.rsi_weight),
            ("macd",             *check_macd(df, direction),                                   confidence_config.macd_weight),
            ("volume",           *check_volume(df),                                            confidence_config.volume_weight),
            ("price_action",     *check_price_action(df, direction),                          confidence_config.price_action_weight),
        ]

        score = confidence_engine.compute(checks)

        if not confidence_engine.passes_threshold(score):
            return None

        # Build confirmations list from passed checks
        confirmations = [name for name, passes, _, _ in checks if passes]

        sl_dist = last_atr * trade_mgmt_config.atr_sl_multiplier
        tp_dist = last_atr * trade_mgmt_config.atr_tp_multiplier

        if direction == Direction.LONG:
            stop_loss = close - sl_dist
            take_profit = close + tp_dist
        else:
            stop_loss = close + sl_dist
            take_profit = close - tp_dist

        # Win probability derived from confidence score (bounded)
        win_prob = min(0.50 + score * 0.30, 0.80)

        return Signal(
            symbol=symbol,
            timestamp=datetime.now(),
            direction=direction,
            strategy_name=self.name,
            entry_price=float(close),
            stop_loss=float(stop_loss),
            take_profit=float(take_profit),
            confirmations=confirmations,
            win_probability=win_prob,
            confidence_score=score,
            indicator_snapshot={
                "ema_fast": float(ema_fast.iloc[i]),
                "ema_slow": float(ema_slow.iloc[i]),
                "atr": float(last_atr),
                "supertrend": float(st_line.iloc[i]),
                "confidence": score,
            },
        )
