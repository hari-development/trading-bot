"""Opening Range Breakout (ORB) — classic intraday breakout off the first N minutes' range."""
from datetime import datetime
from typing import Optional

import pandas as pd

from config.settings import trade_mgmt_config
from core.indicators import opening_range, atr, avg_volume
from core.models import Direction, Signal
from strategies.base import Strategy


class OpeningRangeBreakoutStrategy(Strategy):
    name = "opening_range_breakout"

    def __init__(self, orb_minutes: int = 15):
        self.orb_minutes = orb_minutes

    def required_lookback(self) -> int:
        return 20

    def evaluate(self, symbol: str, df: pd.DataFrame) -> Optional[Signal]:
        if len(df) < self.required_lookback():
            return None
        if not isinstance(df.index, pd.DatetimeIndex):
            return None

        today = df.index[-1].date()
        session_df = df[df.index.date == today]
        if session_df.empty:
            return None

        or_high, or_low = opening_range(session_df, self.orb_minutes)
        if or_high is None:
            return None

        # only trade breakouts that occur after the opening range window closes
        session_start = session_df.index[0]
        window_end = session_start + pd.Timedelta(minutes=self.orb_minutes)
        post_range = session_df[session_df.index > window_end]
        if post_range.empty:
            return None

        i = -1
        close = post_range["close"].iloc[i]
        atr_val = atr(df, 14)
        last_atr = atr_val.iloc[-1]
        if pd.isna(last_atr) or last_atr == 0:
            return None

        vol_avg = avg_volume(df, 20)
        vol_ok = not pd.isna(vol_avg.iloc[-1]) and df["volume"].iloc[-1] >= vol_avg.iloc[-1] * 1.2

        # only fire on the bar that actually crosses (avoid re-signaling every bar while extended)
        if len(post_range) < 2:
            return None
        prev_close = post_range["close"].iloc[-2]

        confirmations = []
        direction = None
        if prev_close <= or_high and close > or_high:
            direction = Direction.LONG
            confirmations.append("broke_opening_range_high")
        elif prev_close >= or_low and close < or_low:
            direction = Direction.SHORT
            confirmations.append("broke_opening_range_low")
        else:
            return None

        if vol_ok:
            confirmations.append("volume_confirms_breakout")
        else:
            return None  # ORB without volume is the classic false-breakout trap

        range_size = or_high - or_low
        if range_size <= 0:
            return None
        confirmations.append("valid_range_size")

        sl_dist = max(last_atr * trade_mgmt_config.atr_sl_multiplier, range_size * 0.3)
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
            win_probability=min(0.5 + 0.05 * len(confirmations), 0.68),
            indicator_snapshot={"or_high": float(or_high), "or_low": float(or_low)},
        )
