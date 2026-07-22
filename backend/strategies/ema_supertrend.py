"""EMA crossover confirmed by SuperTrend direction — trend-following strategy."""
from datetime import datetime
from typing import Optional

import pandas as pd

from config.settings import trade_mgmt_config
from core.indicators import ema, supertrend, atr, adx, avg_volume
from core.models import Direction, Signal
from strategies.base import Strategy


class EmaSupertrendStrategy(Strategy):
    name = "ema_supertrend"

    def __init__(self, fast: int = 9, slow: int = 21, st_period: int = 10, st_mult: float = 3.0):
        self.fast = fast
        self.slow = slow
        self.st_period = st_period
        self.st_mult = st_mult

    def required_lookback(self) -> int:
        return max(self.slow, self.st_period) + 20

    def evaluate(self, symbol: str, df: pd.DataFrame) -> Optional[Signal]:
        if len(df) < self.required_lookback():
            return None

        ema_fast = ema(df["close"], self.fast)
        ema_slow = ema(df["close"], self.slow)
        st_line, st_trend = supertrend(df, self.st_period, self.st_mult)
        atr_val = atr(df, 14)
        adx_val, plus_di, minus_di = adx(df, 14)
        vol_avg = avg_volume(df, 20)

        i = -1
        prev = -2
        close = df["close"].iloc[i]
        last_atr = atr_val.iloc[i]
        if pd.isna(last_atr) or last_atr == 0:
            return None

        bullish_cross = ema_fast.iloc[prev] <= ema_slow.iloc[prev] and ema_fast.iloc[i] > ema_slow.iloc[i]
        bearish_cross = ema_fast.iloc[prev] >= ema_slow.iloc[prev] and ema_fast.iloc[i] < ema_slow.iloc[i]
        st_bullish = st_trend.iloc[i] == 1
        st_bearish = st_trend.iloc[i] == -1
        strong_trend = not pd.isna(adx_val.iloc[i]) and adx_val.iloc[i] >= 20
        vol_ok = not pd.isna(vol_avg.iloc[i]) and df["volume"].iloc[i] >= vol_avg.iloc[i] * 0.8

        confirmations = []
        direction = None

        if bullish_cross and st_bullish:
            direction = Direction.LONG
            confirmations = ["ema_bullish_cross", "supertrend_bullish"]
        elif bearish_cross and st_bearish:
            direction = Direction.SHORT
            confirmations = ["ema_bearish_cross", "supertrend_bearish"]
        else:
            return None

        if strong_trend:
            confirmations.append("adx_confirms_trend")
        if vol_ok:
            confirmations.append("volume_confirms")

        if len(confirmations) < 3:  # need at least trend cross + ST + one more
            return None

        sl_dist = last_atr * trade_mgmt_config.atr_sl_multiplier
        tp_dist = last_atr * trade_mgmt_config.atr_tp_multiplier

        if direction == Direction.LONG:
            stop_loss = close - sl_dist
            take_profit = close + tp_dist
        else:
            stop_loss = close + sl_dist
            take_profit = close - tp_dist

        win_prob = 0.5 + 0.05 * len(confirmations)  # simple heuristic, refined by risk engine

        return Signal(
            symbol=symbol,
            timestamp=datetime.now(),
            direction=direction,
            strategy_name=self.name,
            entry_price=float(close),
            stop_loss=float(stop_loss),
            take_profit=float(take_profit),
            confirmations=confirmations,
            win_probability=min(win_prob, 0.75),
            indicator_snapshot={
                "ema_fast": float(ema_fast.iloc[i]),
                "ema_slow": float(ema_slow.iloc[i]),
                "atr": float(last_atr),
                "adx": float(adx_val.iloc[i]) if not pd.isna(adx_val.iloc[i]) else 0.0,
            },
        )
