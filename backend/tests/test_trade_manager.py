from datetime import datetime, timedelta

from config.settings import trade_mgmt_config
from core.models import Direction, Position, ExitReason
from core.trade_manager import evaluate_position


def make_long_position(entry=100.0, sl=98.0, tp=106.0):
    return Position(
        symbol="TEST", direction=Direction.LONG, entry_price=entry, quantity=10,
        stop_loss=sl, take_profit=tp, entry_time=datetime.now(), strategy_name="test",
    )


def test_stop_loss_triggers_full_exit():
    pos = make_long_position()
    action = evaluate_position(pos, current_price=97.5, current_time=datetime.now())
    assert action.action == "EXIT_FULL"
    assert action.reason.value == "STOP_LOSS"


def test_take_profit_triggers_full_exit():
    pos = make_long_position()
    action = evaluate_position(pos, current_price=106.5, current_time=datetime.now())
    assert action.action == "EXIT_FULL"
    assert action.reason.value == "TAKE_PROFIT"


def test_breakeven_moves_stop_to_entry():
    pos = make_long_position(entry=100, sl=98, tp=106)
    # risk = 2, breakeven trigger at 1R => price = 102
    action = evaluate_position(pos, current_price=102.5, current_time=datetime.now())
    assert action.action == "MOVE_SL"
    assert action.new_stop_loss == 100.0


def test_time_based_exit_after_max_holding():
    pos = make_long_position()
    old_entry_time = datetime.now() - timedelta(minutes=trade_mgmt_config.max_holding_minutes + 1)
    pos.entry_time = old_entry_time
    action = evaluate_position(pos, current_price=101.0, current_time=datetime.now())
    assert action.action == "EXIT_FULL"
    assert action.reason.value == "TIME_EXIT"


def test_hold_when_no_conditions_met():
    pos = make_long_position()
    action = evaluate_position(pos, current_price=100.5, current_time=datetime.now())
    assert action.action == "HOLD"


def test_long_option_position_exits_on_underlying_targets():
    # Long option contract entered at 15.0 premium, but tracking underlying asset (e.g. RELIANCE)
    # Underlying entry: 2500.0, SL: 2480.0, TP: 2540.0, Direction: LONG
    pos = Position(
        symbol="RELIANCE_CE", direction=Direction.LONG, entry_price=15.0, quantity=10,
        stop_loss=10.0, take_profit=20.0, entry_time=datetime.now(), strategy_name="test",
        underlying_symbol="RELIANCE", underlying_entry_price=2500.0,
        underlying_direction=Direction.LONG, underlying_stop_loss=2480.0, underlying_take_profit=2540.0
    )
    
    # 1. Under TP/SL (e.g. underlying is 2510.0) -> HOLD
    action = evaluate_position(pos, current_price=2510.0, current_time=datetime.now())
    assert action.action == "HOLD"
    
    # 2. Underlying reaches TP (e.g. underlying is 2545.0) -> EXIT_FULL (TAKE_PROFIT)
    action = evaluate_position(pos, current_price=2545.0, current_time=datetime.now())
    assert action.action == "EXIT_FULL"
    assert action.reason == ExitReason.TAKE_PROFIT
    
    # 3. Underlying reaches SL (e.g. underlying is 2475.0) -> EXIT_FULL (STOP_LOSS)
    action = evaluate_position(pos, current_price=2475.0, current_time=datetime.now())
    assert action.action == "EXIT_FULL"
    assert action.reason == ExitReason.STOP_LOSS


def test_short_option_position_exits_on_underlying_targets():
    # Put option contract entered at 15.0 premium (option direction LONG), but tracking underlying asset SHORT
    # Underlying entry: 2500.0, SL: 2520.0, TP: 2460.0, Direction: SHORT
    pos = Position(
        symbol="RELIANCE_PE", direction=Direction.LONG, entry_price=15.0, quantity=10,
        stop_loss=10.0, take_profit=20.0, entry_time=datetime.now(), strategy_name="test",
        underlying_symbol="RELIANCE", underlying_entry_price=2500.0,
        underlying_direction=Direction.SHORT, underlying_stop_loss=2520.0, underlying_take_profit=2460.0
    )
    
    # 1. Under TP/SL (e.g. underlying is 2490.0) -> HOLD
    action = evaluate_position(pos, current_price=2490.0, current_time=datetime.now())
    assert action.action == "HOLD"
    
    # 2. Underlying reaches TP (e.g. underlying is 2455.0) -> EXIT_FULL (TAKE_PROFIT)
    action = evaluate_position(pos, current_price=2455.0, current_time=datetime.now())
    assert action.action == "EXIT_FULL"
    assert action.reason == ExitReason.TAKE_PROFIT
    
    # 3. Underlying reaches SL (e.g. underlying is 2525.0) -> EXIT_FULL (STOP_LOSS)
    action = evaluate_position(pos, current_price=2525.0, current_time=datetime.now())
    assert action.action == "EXIT_FULL"
    assert action.reason == ExitReason.STOP_LOSS
