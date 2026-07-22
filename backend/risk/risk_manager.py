"""
Risk Manager — the single source of truth for "are we allowed to trade
right now, and if so, with what size".

This is where the "never blindly re-enter after a loss" requirement is
actually enforced. No strategy or execution code is allowed to bypass it.

State is intentionally kept in-memory + mirrored to a JSON file on every
change so a restart doesn't reset the day's loss count to zero.
"""
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import List, Optional

from config.settings import risk_config
from core.models import ClosedTrade, Signal
from core.regime import Regime, RegimeSnapshot
from utils.logger import get_logger

logger = get_logger("risk_manager")

STATE_FILE = Path("logs/risk_state.json")


@dataclass
class DayState:
    trade_date: str = field(default_factory=lambda: date.today().isoformat())
    starting_equity: float = risk_config.starting_capital
    current_equity: float = risk_config.starting_capital
    peak_equity: float = risk_config.starting_capital
    realized_pnl_today: float = 0.0
    trades_today: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_profit: float = 0.0       # sum of all winning trade P&L
    total_loss: float = 0.0         # sum of all losing trade P&L (negative)
    consecutive_losses: int = 0
    consecutive_wins: int = 0
    cooldown_until: Optional[str] = None      # ISO timestamp
    shutdown_for_day: bool = False
    shutdown_reason: str = ""
    hard_stop: bool = False                   # max drawdown breached → requires manual review
    hard_stop_reason: str = ""

    def to_json(self) -> str:
        return json.dumps(self.__dict__, indent=2)


class RiskManager:
    def __init__(self):
        self.state = self._load_or_init_state()

    # ---------- persistence ----------
    def _load_or_init_state(self) -> DayState:
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text())
                # Add new fields with defaults if loading from older state file
                data.setdefault("winning_trades", 0)
                data.setdefault("losing_trades", 0)
                data.setdefault("total_profit", 0.0)
                data.setdefault("total_loss", 0.0)
                state = DayState(**data)
                if state.trade_date != date.today().isoformat():
                    logger.info(
                        "New trading day detected — resetting daily counters, "
                        "preserving equity/drawdown state."
                    )
                    state = DayState(
                        starting_equity=state.current_equity,
                        current_equity=state.current_equity,
                        peak_equity=state.peak_equity,
                    )
                return state
            except Exception as e:
                logger.error(f"Failed to load risk state, reinitializing: {e}")
        return DayState()

    def _persist(self):
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(self.state.to_json())

    # ---------- kill switch ----------
    def kill_switch_active(self) -> bool:
        return os.path.exists(risk_config.kill_switch_file)

    def _check_day_rollover(self):
        today_str = date.today().isoformat()
        if self.state.trade_date != today_str:
            logger.info(
                f"New trading day detected ({today_str}) — resetting daily profit target & counters, "
                f"preserving equity/drawdown state."
            )
            self.state = DayState(
                trade_date=today_str,
                starting_equity=self.state.current_equity,
                current_equity=self.state.current_equity,
                peak_equity=self.state.peak_equity,
                hard_stop=self.state.hard_stop,
                hard_stop_reason=self.state.hard_stop_reason,
            )
            self._persist()

    def get_daily_profit_target(self) -> float:
        if getattr(risk_config, "use_fixed_daily_profit_target", True) and getattr(risk_config, "max_daily_profit_target", 5000.0) > 0:
            return risk_config.max_daily_profit_target
        return self.state.starting_equity * risk_config.max_daily_profit_pct / 100

    # ---------- core gate ----------
    def can_trade(self) -> tuple[bool, str]:
        self._check_day_rollover()
        if self.kill_switch_active():
            return False, "KILL_SWITCH_ACTIVE"
        if self.state.hard_stop:
            return False, f"HARD_STOP: {self.state.hard_stop_reason}"
        if self.state.shutdown_for_day:
            return False, f"DAILY_SHUTDOWN: {self.state.shutdown_reason}"
        if self.state.cooldown_until:
            cooldown_end = datetime.fromisoformat(self.state.cooldown_until)
            if datetime.now() < cooldown_end:
                remaining = max(0, (cooldown_end - datetime.now()).seconds // 60)
                return False, f"COOLDOWN_ACTIVE ({remaining} min remaining)"
            else:
                # Cooldown expired — clear it
                self.state.cooldown_until = None
                self._persist()
        if self.state.trades_today >= risk_config.max_trades_per_day:
            return False, "MAX_TRADES_PER_DAY_REACHED"

        daily_loss_limit = -abs(self.state.starting_equity * risk_config.max_daily_loss_pct / 100)
        if self.state.realized_pnl_today <= daily_loss_limit:
            self._shutdown_day(f"max_daily_loss_hit({self.state.realized_pnl_today:.2f})")
            return False, "DAILY_LOSS_LIMIT_HIT"

        daily_profit_limit = self.get_daily_profit_target()
        if self.state.realized_pnl_today >= daily_profit_limit:
            self._shutdown_day(f"max_daily_profit_hit({self.state.realized_pnl_today:.2f})")
            return False, "DAILY_PROFIT_TARGET_HIT"

        drawdown_pct = (
            (self.state.peak_equity - self.state.current_equity) / self.state.peak_equity * 100
        )
        if drawdown_pct >= risk_config.max_drawdown_pct:
            self.state.hard_stop = True
            self.state.hard_stop_reason = f"max_drawdown_breached({drawdown_pct:.2f}%)"
            self._persist()
            logger.critical(
                f"HARD STOP: {self.state.hard_stop_reason}. "
                "Manual review required before resuming."
            )
            return False, "MAX_DRAWDOWN_HARD_STOP"

        return True, "OK"

    def _shutdown_day(self, reason: str):
        self.state.shutdown_for_day = True
        self.state.shutdown_reason = reason
        self._persist()
        logger.warning(f"Trading halted for today: {reason}")

    # ---------- position sizing (anti-martingale) ----------
    def calculate_position_size(self, signal: Signal, regime: RegimeSnapshot) -> int:
        """
        Base risk = max_risk_per_trade_pct of current equity.
        Size is REDUCED after consecutive losses, restored only after a
        run of wins — the opposite of martingale doubling. Never scales
        UP after a loss, ever.
        """
        risk_amount = self.state.current_equity * risk_config.max_risk_per_trade_pct / 100

        if self.state.consecutive_losses >= risk_config.losses_before_size_reduction:
            reduction = risk_config.size_reduction_after_loss_pct / 100
            risk_amount *= (1 - reduction)
            logger.info(
                f"Position size reduced {reduction * 100:.0f}% after "
                f"{self.state.consecutive_losses} consecutive losses."
            )

        # further trim in high-volatility-adjacent conditions
        if regime.regime in (Regime.TRENDING_UP, Regime.TRENDING_DOWN) and regime.atr_pct > 2.0:
            risk_amount *= 0.75

        if signal.risk_per_share <= 0:
            return 0

        qty = int(risk_amount // signal.risk_per_share)
        return max(qty, 0)

    def validate_risk_reward(self, signal: Signal) -> bool:
        return signal.risk_reward_ratio >= risk_config.min_risk_reward_ratio

    # ---------- post-trade update: "intelligent recovery" state machine ----------
    def register_closed_trade(self, trade: ClosedTrade):
        self._check_day_rollover()
        self.state.realized_pnl_today += trade.pnl
        self.state.current_equity += trade.pnl
        self.state.peak_equity = max(self.state.peak_equity, self.state.current_equity)
        self.state.trades_today += 1

        daily_profit_target = self.get_daily_profit_target()
        if self.state.realized_pnl_today >= daily_profit_target and not self.state.shutdown_for_day:
            logger.info(
                f"Daily profit target reached/exceeded: ₹{self.state.realized_pnl_today:.2f} >= ₹{daily_profit_target:.2f}. "
                f"Halting trading for today. Trading will restart automatically on the next date."
            )
            self._shutdown_day(f"max_daily_profit_hit({self.state.realized_pnl_today:.2f})")

        if trade.pnl < 0:
            self.state.consecutive_losses += 1
            self.state.consecutive_wins = 0
            self.state.losing_trades += 1
            self.state.total_loss += trade.pnl  # pnl is already negative
            logger.info(
                f"Loss registered on {trade.symbol} ({trade.strategy_name}): "
                f"{trade.pnl:.2f}. Consecutive losses: {self.state.consecutive_losses}"
            )
            if self.state.consecutive_losses >= risk_config.max_consecutive_losses:
                cooldown_end = datetime.now() + timedelta(
                    minutes=risk_config.cooldown_after_max_losses_minutes
                )
                self.state.cooldown_until = cooldown_end.isoformat()
                logger.warning(
                    f"Max consecutive losses ({risk_config.max_consecutive_losses}) reached. "
                    f"Cooling down until {cooldown_end.strftime('%H:%M:%S')}. "
                    "No new trades until then — market regime re-evaluated on resume."
                )
        else:
            self.state.consecutive_wins += 1
            self.state.winning_trades += 1
            self.state.total_profit += trade.pnl
            if self.state.consecutive_wins >= risk_config.size_recovery_wins_required:
                if self.state.consecutive_losses > 0:
                    logger.info(
                        "Consecutive win streak achieved — position sizing restored to normal."
                    )
                self.state.consecutive_losses = 0
            logger.info(
                f"Win registered on {trade.symbol} ({trade.strategy_name}): "
                f"+{trade.pnl:.2f}. Consecutive wins: {self.state.consecutive_wins}"
            )

        self._persist()

    def emergency_stop(self, reason: str = "manual_kill_switch"):
        Path(risk_config.kill_switch_file).parent.mkdir(parents=True, exist_ok=True)
        Path(risk_config.kill_switch_file).write_text(
            f"Killed at {datetime.now().isoformat()}: {reason}"
        )
        logger.critical(f"EMERGENCY STOP TRIGGERED: {reason}")

    def status_summary(self) -> dict:
        total_closed = self.state.winning_trades + self.state.losing_trades
        win_rate = (self.state.winning_trades / total_closed * 100) if total_closed > 0 else 0.0
        avg_profit = (self.state.total_profit / self.state.winning_trades) \
            if self.state.winning_trades > 0 else 0.0
        avg_loss = (self.state.total_loss / self.state.losing_trades) \
            if self.state.losing_trades > 0 else 0.0

        drawdown_pct = 0.0
        if self.state.peak_equity > 0:
            drawdown_pct = round(
                (self.state.peak_equity - self.state.current_equity)
                / self.state.peak_equity * 100, 2
            )

        return {
            "equity": round(self.state.current_equity, 2),
            "starting_equity": round(self.state.starting_equity, 2),
            "realized_pnl_today": round(self.state.realized_pnl_today, 2),
            "trades_today": self.state.trades_today,
            "winning_trades": self.state.winning_trades,
            "losing_trades": self.state.losing_trades,
            "win_rate_pct": round(win_rate, 1),
            "avg_profit": round(avg_profit, 2),
            "avg_loss": round(avg_loss, 2),
            "consecutive_losses": self.state.consecutive_losses,
            "consecutive_wins": self.state.consecutive_wins,
            "cooldown_until": self.state.cooldown_until,
            "shutdown_for_day": self.state.shutdown_for_day,
            "shutdown_reason": self.state.shutdown_reason,
            "hard_stop": self.state.hard_stop,
            "kill_switch_active": self.kill_switch_active(),
            "drawdown_pct": drawdown_pct,
        }
