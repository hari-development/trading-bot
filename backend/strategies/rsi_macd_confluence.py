"""RSI + MACD confluence — momentum shift confirmation, works best in ranging/early-trend regimes."""
from datetime import datetime
from typing import Optional

import pandas as pd

from config.settings import trade_mgmt_config
from core.indicators import rsi, macd, atr, bollinger_bands
from core.models import Direction, Signal
from strategies.base import Strategy


class RsiMacdConfluenceStrategy(Strategy):
    name = "rsi_macd_confluence"

    def required_lookback(self) -> int:
        return 40

    def evaluate(self, symbol: str, df: pd.DataFrame) -> Optional[Signal]:
        if len(df) < self.required_lookback():
            return None

        rsi_series = rsi(df["close"], 14)
        macd_line, signal_line, hist = macd(df["close"])
        upper_bb, mid_bb, lower_bb = bollinger_bands(df["close"], 20, 2.0)
        atr_val = atr(df, 14)

        i, prev = -1, -2
        close = df["close"].iloc[i]
        last_atr = atr_val.iloc[i]
        if pd.isna(last_atr) or last_atr == 0 or pd.isna(rsi_series.iloc[i]):
            return None

        macd_cross_up = hist.iloc[prev] <= 0 and hist.iloc[i] > 0
        macd_cross_down = hist.iloc[prev] >= 0 and hist.iloc[i] < 0
        rsi_oversold_recovery = rsi_series.iloc[prev] < 40 and rsi_series.iloc[i] >= 40
        rsi_overbought_reversal = rsi_series.iloc[prev] > 60 and rsi_series.iloc[i] <= 60
        near_lower_band = not pd.isna(lower_bb.iloc[i]) and close <= lower_bb.iloc[i] * 1.01
        near_upper_band = not pd.isna(upper_bb.iloc[i]) and close >= upper_bb.iloc[i] * 0.99

        confirmations = []
        direction = None

        if macd_cross_up and rsi_oversold_recovery:
            direction = Direction.LONG
            confirmations = ["macd_bullish_cross", "rsi_oversold_recovery"]
            if near_lower_band:
                confirmations.append("bb_lower_band_bounce")
        elif macd_cross_down and rsi_overbought_reversal:
            direction = Direction.SHORT
            confirmations = ["macd_bearish_cross", "rsi_overbought_reversal"]
            if near_upper_band:
                confirmations.append("bb_upper_band_rejection")
        else:
            return None

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
            win_probability=min(0.5 + 0.05 * len(confirmations), 0.7),
            indicator_snapshot={"rsi": float(rsi_series.iloc[i]), "macd_hist": float(hist.iloc[i])},
        )
