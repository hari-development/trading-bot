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
class ConfidenceConfig:
    """
    Weighted confidence scoring for the AI decision layer.
    Each weight represents the fractional contribution of that indicator
    to the final 0.0-1.0 confidence score. Weights should sum to 1.0.
    Only trade when confidence_score >= min_confidence_threshold.
    """
    ema_trend_weight: float = 0.20
    supertrend_weight: float = 0.15
    vwap_weight: float = 0.10
    adx_weight: float = 0.10
    rsi_weight: float = 0.10
    macd_weight: float = 0.10
    volume_weight: float = 0.10
    price_action_weight: float = 0.15

    min_confidence_threshold: float = 0.60   # minimum to allow entry
    high_confidence_threshold: float = 0.80  # full position size at this level


@dataclass
class TradeScoreConfig:
    """
    0-100 trade quality scoring system.
    Trades below min_trade_score are rejected regardless of strategy signal.
    """
    # Score component maximums (sum = 100)
    regime_max: int = 15
    trend_quality_max: int = 20
    momentum_max: int = 20
    volume_max: int = 15
    htf_alignment_max: int = 15
    confirmation_count_max: int = 15

    min_trade_score: int = 60            # reject if score < 60/100


@dataclass
class AdaptiveLearningConfig:
    """
    Tracks strategy performance per regime and adjusts selection preference.
    """
    enabled: bool = True
    min_samples_before_adjustment: int = 20
    weight_decay_factor: float = 0.95
    persistence_file: str = "logs/adaptive_state.json"


@dataclass
class DatabaseConfig:
    """Trade journal and analytics database."""
    enabled: bool = True
    journal_db_path: str = "logs/trade_journal.db"
    backtest_results_path: str = "logs/backtest_results/"


@dataclass
class RiskConfig:
    # Capital
    starting_capital: float = 20000.0          # INR (₹20k — paper mode or option buying)

    # Per-trade risk — FIXED from dangerous 10% → conservative 1.5%
    max_risk_per_trade_pct: float = 1.5           # % of current equity risked per trade
    min_risk_reward_ratio: float = 1.5            # reject trades below this

    # Daily circuit breakers
    max_daily_loss_pct: float = 2.0               # % of CURRENT equity → stop trading for the day
    max_daily_profit_target: float = 3000.0       # INR fixed daily profit target (₹3,000)
    use_fixed_daily_profit_target: bool = True     # Use fixed INR target if True
    max_daily_profit_pct: float = 5.0             # % of capital → fallback daily profit target
    max_trades_per_day: int = 15                  # reduced from 30 to avoid overtrading
    max_trades_per_day_per_symbol: int = 2        # per-symbol daily trade cap

    # Concurrent open positions
    max_open_positions: int = 2                   # never hold more than 2 at once
    max_capital_usage_pct: float = 30.0           # cap total margin used — NOW ENFORCED

    # Consecutive loss handling (anti-martingale)
    max_consecutive_losses: int = 2               # after this many, force a cooldown
    cooldown_after_max_losses_minutes: int = 60
    size_reduction_after_loss_pct: float = 50.0  # cut position size by this % after N losses
    losses_before_size_reduction: int = 2         # start reducing after N consecutive losses
    size_recovery_wins_required: int = 2          # consecutive wins needed to restore full size

    # Revenge trading guard
    max_losses_per_hour: int = 3                  # force extended cooldown if >3 losses in 1 hour
    revenge_cooldown_minutes: int = 120

    # Drawdown
    max_drawdown_pct: float = 10.0               # from equity peak → full shutdown, manual review

    # Kill switch
    kill_switch_file: str = "logs/KILL_SWITCH"

    # Slippage protection
    max_slippage_pct: float = 0.5               # reject fills >0.5% from signal price


@dataclass
class QualityFilterConfig:
    min_confirmations: int = 3                   # raised: need 3 confirmations for quality entries
    max_atr_pct_of_price: float = 3.0            # reject if ATR% too high (volatility blowout)
    min_avg_volume: int = 10000                  # min 20-period avg volume (liquidity filter)
    min_win_probability: float = 0.60            # minimum signal win probability
    avoid_first_minutes_after_open: int = 15     # skip opening whipsaw
    avoid_last_minutes_before_close: int = 15    # avoid EOD volatility
    enable_news_filter: bool = True              # block entries during macro events
    min_trade_score: int = 60                    # 0-100 quality score gate


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
    min_agreeing_timeframes: int = 1   # at least 1 HTF must agree with signal direction

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
    sl_pct: float = 30.0                        # Stop loss % on option premium (widened to 30%)
    tp_pct: float = 50.0                        # Take profit % on option premium
    expiry_preference: str = "weekly"           # weekly | monthly
    trail_premium_sl: bool = True               # Trail option premium SL once trade turns profitable
    premium_trail_activation_pct: float = 15.0  # Activate trailing SL once option premium reaches +15% profit
    premium_trail_pct: float = 8.0              # Trail 8% below peak premium


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
    poll_interval_seconds: int = 5              # 5-second tick — balanced between responsiveness and API load
    log_dir: str = "logs"
    broker: str = "kite"                        # kite | paper


risk_config = RiskConfig()
quality_config = QualityFilterConfig()
trade_mgmt_config = TradeManagementConfig()
mtf_config = MultiTimeframeConfig()
strategy_config = StrategyConfig()
system_config = SystemConfig()
option_config = OptionConfig()
confidence_config = ConfidenceConfig()
trade_score_config = TradeScoreConfig()
adaptive_config = AdaptiveLearningConfig()
db_config = DatabaseConfig()
