"""VWAP breakout strategy — price reclaiming/losing VWAP with volume confirmation."""
from datetime import datetime
from typing import Optional

import pandas as pd

from config.settings import trade_mgmt_config
from core.indicators import vwap, atr, avg_volume
from core.models import Direction, Signal
from strategies.base import Strategy


class VwapBreakoutStrategy(Strategy):
    name = "vwap_breakout"

    def required_lookback(self) -> int:
        return 30

    def evaluate(self, symbol: str, df: pd.DataFrame) -> Optional[Signal]:
        if len(df) < self.required_lookback():
            return None

        vwap_series = vwap(df)
        atr_val = atr(df, 14)
        vol_avg = avg_volume(df, 20)

        i, prev = -1, -2
        close = df["close"].iloc[i]
        last_atr = atr_val.iloc[i]
        if pd.isna(last_atr) or last_atr == 0 or pd.isna(vwap_series.iloc[i]):
            return None

        crossed_above = df["close"].iloc[prev] <= vwap_series.iloc[prev] and close > vwap_series.iloc[i]
        crossed_below = df["close"].iloc[prev] >= vwap_series.iloc[prev] and close < vwap_series.iloc[i]
        vol_spike = not pd.isna(vol_avg.iloc[i]) and df["volume"].iloc[i] >= vol_avg.iloc[i] * 1.3

        if not vol_spike:
            return None  # VWAP breakout without volume is a trap, hard filter

        confirmations = ["volume_spike"]
        direction = None
        if crossed_above:
            direction = Direction.LONG
            confirmations.append("vwap_reclaim")
        elif crossed_below:
            direction = Direction.SHORT
            confirmations.append("vwap_breakdown")
        else:
            return None

        # candle body confirmation
        body = abs(df["close"].iloc[i] - df["open"].iloc[i])
        candle_range = df["high"].iloc[i] - df["low"].iloc[i]
        if candle_range > 0 and body / candle_range >= 0.6:
            confirmations.append("strong_candle_body")

        if len(confirmations) < 2:
            return None

        sl_dist = last_atr * trade_mgmt_config.atr_sl_multiplier
        tp_dist = last_atr * trade_mgmt_config.atr_tp_multiplier
        if direction == Direction.LONG:
            stop_loss, take_profit = close - sl_dist, close + tp_dist
        else:
            stop_loss, take_profit = close + sl_dist, close - tp_dist

        return Signal(
            symbol=symbol,
            timestamp=datetime.now(),
            direction=direction,
            strategy_name=self.name,
            entry_price=float(close),
            stop_loss=float(stop_loss),
            take_profit=float(take_profit),
            confirmations=confirmations,
            win_probability=min(0.5 + 0.06 * len(confirmations), 0.72),
            indicator_snapshot={"vwap": float(vwap_series.iloc[i]), "atr": float(last_atr)},
        )
