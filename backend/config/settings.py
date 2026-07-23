"""
Central configuration for the trading bot.
Every risk limit, threshold, and toggle lives here — nothing hardcoded
in strategy/risk/execution modules. Edit this file to tune behavior.
"""
from dataclasses import dataclass, field
from typing import Dict, List


# ---------------------------------------------------------------------------
# Index / futures lot sizes — keeps engine.py free of hardcoded if/elif chains
# ---------------------------------------------------------------------------
LOT_SIZES: Dict[str, int] = {
    "NIFTY":       25,
    "BANKNIFTY":   15,
    "FINNIFTY":    40,
    "MIDCPNIFTY":  75,
    "SENSEX":      10,
    "RELIANCE":    250,
    "HDFCBANK":    400,
    "ICICIBANK":   700,
    "INFY":        400,
    "TCS":         175,
}


@dataclass
class RiskConfig:
    # Capital
    starting_capital: float = 20000.0          # INR (₹20k — paper mode or option buying)

    # Per-trade risk
    max_risk_per_trade_pct: float = 10.0          # % of capital risked per trade (Increased to 10% to hit 3k target faster)
    min_risk_reward_ratio: float = 1.5            # reject trades below this

    # Daily circuit breakers
    max_daily_loss_pct: float = 2.0               # % of capital → stop trading for the day
    max_daily_profit_target: float = 3000.0       # INR fixed daily profit target (₹3,000)
    use_fixed_daily_profit_target: bool = True     # Use fixed INR target if True
    max_daily_profit_pct: float = 5.0             # % of capital → fallback daily profit target
    max_trades_per_day: int = 30

    # Concurrent open positions
    max_open_positions: int = 2                   # never hold more than 2 at once
    max_capital_usage_pct: float = 30.0           # cap total margin used

    # Consecutive loss handling (anti-martingale)
    max_consecutive_losses: int = 2               # after this many, force a cooldown
    cooldown_after_max_losses_minutes: int = 60
    size_reduction_after_loss_pct: float = 50.0  # cut position size by this % after N losses
    losses_before_size_reduction: int = 2         # start reducing after N consecutive losses
    size_recovery_wins_required: int = 2          # consecutive wins needed to restore full size

    # Drawdown
    max_drawdown_pct: float = 10.0               # from equity peak → full shutdown, manual review

    # Kill switch
    kill_switch_file: str = "logs/KILL_SWITCH"


@dataclass
class QualityFilterConfig:
    min_confirmations: int = 2                   # min indicators that must agree (EMA-ST base = 2)
    max_atr_pct_of_price: float = 3.0            # reject if ATR% too high (volatility blowout)
    min_avg_volume: int = 10000                  # min 20-period avg volume (liquidity filter)
    min_win_probability: float = 0.60            # raised back to 0.60 to avoid low-quality signals causing losses
    avoid_first_minutes_after_open: int = 15     # skip opening whipsaw (raised from 5→15)
    avoid_last_minutes_before_close: int = 15    # avoid EOD volatility (raised from 10→15)
    enable_news_filter: bool = True              # block entries during macro events


@dataclass
class TradeManagementConfig:
    atr_sl_multiplier: float = 1.5              # stop-loss = entry ±  ATR × multiplier
    atr_tp_multiplier: float = 3.0             # take-profit target
    trailing_activation_rr: float = 0.8        # start trailing earlier
    trailing_atr_multiplier: float = 1.2
    partial_booking_rr: float = 1.5            # book partial profit at this R multiple
    partial_booking_pct: float = 50.0          # % of position to book
    breakeven_trigger_rr: float = 0.5          # move SL to breakeven faster to protect capital
    max_holding_minutes: int = 180             # time-based exit for intraday


@dataclass
class MultiTimeframeConfig:
    """
    MTF confirmation gate.
    A 5m signal is only accepted when the regime on the required higher
    timeframes does not actively OPPOSE the signal direction.

    Agreement logic:
      - If any required TF shows a HARD OPPOSING trend (e.g., TRENDING_DOWN
        while signal is LONG), entry is blocked — hard veto.
      - If at least `min_agreeing_timeframes` TFs show the SAME trend
        direction as the signal, entry is confirmed.
      - A TF that is RANGING or HIGH_VOLATILITY counts as NEUTRAL — it
        does NOT veto, but also does NOT count as agreement.

    min_agreeing_timeframes=1 means: "at least one higher TF must be
    trending with us — we won't fight an established trend, but we don't
    need BOTH timeframes to agree (that is too strict early in a session)."
    """
    enabled: bool = True
    required_timeframes: list = None
    min_agreeing_timeframes: int = 0   # ← veto-only: block only hard opposing trends; RANGING = neutral

    def __post_init__(self):
        if self.required_timeframes is None:
            self.required_timeframes = ["15minute", "60minute"]


@dataclass
class StrategyConfig:
    enabled_strategies: List[str] = field(default_factory=lambda: [
        "ema_supertrend", "vwap_breakout", "rsi_macd_confluence", "opening_range_breakout"
    ])
    primary_timeframe: str = "5minute"
    lookback_bars: int = 150


@dataclass
class OptionConfig:
    enabled: bool = True                        # Buy options instead of direct equities
    strike_selection: str = "ITM1"              # ITM1 (In-The-Money 1 strike, higher delta ~0.65) | ATM | OTM1
    sl_pct: float = 25.0                        # Stop loss % on option premium (25%)
    tp_pct: float = 50.0                        # Take profit % on option premium (50%)
    expiry_preference: str = "weekly"           # weekly | monthly
    trail_premium_sl: bool = True               # Trail option premium SL once trade turns profitable
    premium_trail_activation_pct: float = 10.0  # Activate trailing SL once option premium reaches +10% profit
    premium_trail_pct: float = 5.0              # Trail premium SL 5% below peak premium


@dataclass
class SystemConfig:
    mode: str = "PAPER"                         # PAPER | LIVE | BACKTEST
    watchlist: List[str] = field(default_factory=lambda: [
        # Most liquid NSE F&O indices and stocks
        "NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "MIDCPNIFTY",
        "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS"
    ])
    market_open: str = "09:15"
    market_close: str = "15:30"
    poll_interval_seconds: int = 3              # Fast 3-second tick loop for instant stop-loss checks
    log_dir: str = "logs"
    broker: str = "kite"                        # kite | paper


risk_config = RiskConfig()
quality_config = QualityFilterConfig()
trade_mgmt_config = TradeManagementConfig()
mtf_config = MultiTimeframeConfig()
strategy_config = StrategyConfig()
system_config = SystemConfig()
option_config = OptionConfig()
