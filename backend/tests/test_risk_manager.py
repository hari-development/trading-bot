import os
import shutil
from datetime import datetime, timedelta

import pytest

from config.settings import risk_config
from core.models import ClosedTrade, Direction, ExitReason, Signal
from core.regime import Regime, RegimeSnapshot
from risk.risk_manager import RiskManager, STATE_FILE


@pytest.fixture(autouse=True)
def clean_state():
    if STATE_FILE.exists():
        STATE_FILE.unlink()
    if os.path.exists(risk_config.kill_switch_file):
        os.remove(risk_config.kill_switch_file)
    yield
    if STATE_FILE.exists():
        STATE_FILE.unlink()
    if os.path.exists(risk_config.kill_switch_file):
        os.remove(risk_config.kill_switch_file)


def make_signal(entry=100.0, sl=98.0, tp=106.0) -> Signal:
    return Signal(
        symbol="TEST", timestamp=datetime.now(), direction=Direction.LONG,
        strategy_name="test_strategy", entry_price=entry, stop_loss=sl, take_profit=tp,
        confirmations=["a", "b", "c"], win_probability=0.6,
    )


def make_regime(regime=Regime.TRENDING_UP) -> RegimeSnapshot:
    return RegimeSnapshot(regime=regime, adx_value=25, atr_pct=1.0, plus_di=30, minus_di=15)


def test_position_size_never_negative():
    rm = RiskManager()
    signal = make_signal()
    qty = rm.calculate_position_size(signal, make_regime())
    assert qty >= 0


def test_position_size_reduces_after_consecutive_losses():
    rm = RiskManager()
    signal = make_signal()
    regime = make_regime()
    baseline_qty = rm.calculate_position_size(signal, regime)

    for _ in range(risk_config.losses_before_size_reduction):
        losing_trade = ClosedTrade(
            symbol="TEST", direction=Direction.LONG, entry_price=100, exit_price=98,
            quantity=10, entry_time=datetime.now(), exit_time=datetime.now(),
            strategy_name="test_strategy", exit_reason=ExitReason.STOP_LOSS,
            pnl=-20, pnl_pct=-2.0,
        )
        rm.register_closed_trade(losing_trade)

    reduced_qty = rm.calculate_position_size(signal, regime)
    assert reduced_qty <= baseline_qty, "Position size must shrink after consecutive losses, never grow (anti-martingale)"


def test_never_doubles_size_after_loss():
    """Explicitly guards against any martingale-style doubling regression."""
    rm = RiskManager()
    signal = make_signal()
    regime = make_regime()
    qty_before = rm.calculate_position_size(signal, regime)

    losing_trade = ClosedTrade(
        symbol="TEST", direction=Direction.LONG, entry_price=100, exit_price=98,
        quantity=10, entry_time=datetime.now(), exit_time=datetime.now(),
        strategy_name="test_strategy", exit_reason=ExitReason.STOP_LOSS, pnl=-20, pnl_pct=-2.0,
    )
    rm.register_closed_trade(losing_trade)
    qty_after = rm.calculate_position_size(signal, regime)
    assert qty_after <= qty_before * 1.5  # sanity: never scales up aggressively, let alone doubles


def test_cooldown_triggers_after_max_consecutive_losses():
    rm = RiskManager()
    for _ in range(risk_config.max_consecutive_losses):
        losing_trade = ClosedTrade(
            symbol="TEST", direction=Direction.LONG, entry_price=100, exit_price=98,
            quantity=10, entry_time=datetime.now(), exit_time=datetime.now(),
            strategy_name="test_strategy", exit_reason=ExitReason.STOP_LOSS, pnl=-20, pnl_pct=-2.0,
        )
        rm.register_closed_trade(losing_trade)

    can_trade, reason = rm.can_trade()
    assert not can_trade
    assert "COOLDOWN" in reason


def test_daily_loss_limit_shuts_down_trading():
    rm = RiskManager()
    big_loss = ClosedTrade(
        symbol="TEST", direction=Direction.LONG, entry_price=100, exit_price=50,
        quantity=1000, entry_time=datetime.now(), exit_time=datetime.now(),
        strategy_name="test_strategy", exit_reason=ExitReason.STOP_LOSS,
        pnl=-(risk_config.starting_capital * risk_config.max_daily_loss_pct / 100) * 1.5,
        pnl_pct=-50.0,
    )
    rm.register_closed_trade(big_loss)
    can_trade, reason = rm.can_trade()
    assert not can_trade
    assert "DAILY_LOSS_LIMIT_HIT" in reason or "DAILY_SHUTDOWN" in reason


def test_kill_switch_blocks_trading():
    rm = RiskManager()
    rm.emergency_stop("test")
    can_trade, reason = rm.can_trade()
    assert not can_trade
    assert reason == "KILL_SWITCH_ACTIVE"
    os.remove(risk_config.kill_switch_file)


def test_win_streak_restores_full_size():
    rm = RiskManager()
    for _ in range(risk_config.losses_before_size_reduction):
        rm.register_closed_trade(ClosedTrade(
            symbol="TEST", direction=Direction.LONG, entry_price=100, exit_price=98,
            quantity=10, entry_time=datetime.now(), exit_time=datetime.now(),
            strategy_name="test_strategy", exit_reason=ExitReason.STOP_LOSS, pnl=-20, pnl_pct=-2.0,
        ))
    assert rm.state.consecutive_losses > 0

    for _ in range(risk_config.size_recovery_wins_required):
        rm.register_closed_trade(ClosedTrade(
            symbol="TEST", direction=Direction.LONG, entry_price=100, exit_price=105,
            quantity=10, entry_time=datetime.now(), exit_time=datetime.now(),
            strategy_name="test_strategy", exit_reason=ExitReason.TAKE_PROFIT, pnl=50, pnl_pct=5.0,
        ))
    assert rm.state.consecutive_losses == 0


def test_fixed_daily_profit_target_5000_shuts_down_day():
    rm = RiskManager()
    profit_trade = ClosedTrade(
        symbol="TEST", direction=Direction.LONG, entry_price=100, exit_price=152,
        quantity=100, entry_time=datetime.now(), exit_time=datetime.now(),
        strategy_name="test_strategy", exit_reason=ExitReason.TAKE_PROFIT,
        pnl=5200.0, pnl_pct=52.0,
    )
    rm.register_closed_trade(profit_trade)
    can_trade, reason = rm.can_trade()
    assert not can_trade
    assert "DAILY_PROFIT_TARGET_HIT" in reason or "DAILY_SHUTDOWN" in reason
    assert rm.state.realized_pnl_today == 5200.0


def test_day_rollover_resets_profit_shutdown_on_next_date():
    rm = RiskManager()
    # Hit daily profit shutdown
    profit_trade = ClosedTrade(
        symbol="TEST", direction=Direction.LONG, entry_price=100, exit_price=150,
        quantity=100, entry_time=datetime.now(), exit_time=datetime.now(),
        strategy_name="test_strategy", exit_reason=ExitReason.TAKE_PROFIT,
        pnl=5000.0, pnl_pct=50.0,
    )
    rm.register_closed_trade(profit_trade)
    assert rm.state.shutdown_for_day is True

    # Simulate date rollover by setting trade_date to yesterday
    rm.state.trade_date = "2020-01-01"
    rm._persist()

    # Next call to can_trade should detect new date, reset counters, and allow trading again
    can_trade, reason = rm.can_trade()
    assert can_trade is True
    assert reason == "OK"
    assert rm.state.shutdown_for_day is False
    assert rm.state.realized_pnl_today == 0.0

