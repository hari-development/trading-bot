"""
Trade Manager — owns every open Position's lifecycle after entry:
stop-loss / take-profit checks, trailing stop, partial profit booking,
breakeven adjustment, and time-based exit. Pure logic, no order placement
(that's execution/broker.py's job) — this module decides WHAT should
happen, the broker layer decides HOW to execute it.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from config.settings import trade_mgmt_config
from core.models import Direction, ExitReason, Position


@dataclass
class ManagementAction:
    action: str  # "HOLD" | "EXIT_FULL" | "EXIT_PARTIAL" | "MOVE_SL"
    reason: Optional[ExitReason] = None
    new_stop_loss: Optional[float] = None
    exit_quantity: Optional[int] = None


def _r_multiple(position: Position, current_price: float) -> float:
    if position.underlying_symbol is not None:
        risk = abs(position.underlying_entry_price - position.underlying_stop_loss)
        if risk == 0:
            return 0.0
        if position.underlying_direction == Direction.LONG:
            return (current_price - position.underlying_entry_price) / risk
        return (position.underlying_entry_price - current_price) / risk

    risk = abs(position.entry_price - position.stop_loss)
    if risk == 0:
        return 0.0
    if position.direction == Direction.LONG:
        return (current_price - position.entry_price) / risk
    return (position.entry_price - current_price) / risk


def evaluate_position(position: Position, current_price: float, current_time: datetime) -> ManagementAction:
    use_underlying = position.underlying_symbol is not None
    price = current_price
    sl = position.underlying_stop_loss if use_underlying else position.stop_loss
    tp = position.underlying_take_profit if use_underlying else position.take_profit
    eval_direction = position.underlying_direction if use_underlying else position.direction

    # 1. Hard stop-loss / take-profit
    if eval_direction == Direction.LONG:
        if price <= sl:
            return ManagementAction("EXIT_FULL", ExitReason.STOP_LOSS)
        if price >= tp:
            return ManagementAction("EXIT_FULL", ExitReason.TAKE_PROFIT)
        position.highest_favorable_price = max(position.highest_favorable_price, price)
    else:
        if price >= sl:
            return ManagementAction("EXIT_FULL", ExitReason.STOP_LOSS)
        if price <= tp:
            return ManagementAction("EXIT_FULL", ExitReason.TAKE_PROFIT)
        position.highest_favorable_price = min(position.highest_favorable_price, price)

    r_mult = _r_multiple(position, price)

    # 2. Time-based exit
    holding_minutes = (current_time - position.entry_time).total_seconds() / 60
    if holding_minutes >= trade_mgmt_config.max_holding_minutes:
        return ManagementAction("EXIT_FULL", ExitReason.TIME_EXIT)

    # 3. Breakeven stop adjustment (once favorable enough, remove downside risk)
    if not position.breakeven_applied and r_mult >= trade_mgmt_config.breakeven_trigger_rr:
        position.breakeven_applied = True
        new_sl = position.underlying_entry_price if use_underlying else position.entry_price
        return ManagementAction("MOVE_SL", new_stop_loss=new_sl)

    # 4. Partial profit booking
    if not position.partial_booked and r_mult >= trade_mgmt_config.partial_booking_rr:
        position.partial_booked = True
        booked_qty = max(int(position.original_quantity * trade_mgmt_config.partial_booking_pct / 100), 1)
        booked_qty = min(booked_qty, position.quantity)
        return ManagementAction("EXIT_PARTIAL", ExitReason.PARTIAL_BOOK, exit_quantity=booked_qty)

    # 5. Trailing stop, activates once trade is sufficiently in profit
    if r_mult >= trade_mgmt_config.trailing_activation_rr:
        if use_underlying:
            risk_unit = abs(position.underlying_entry_price - position.underlying_stop_loss)
            trail_distance = risk_unit * trade_mgmt_config.trailing_atr_multiplier
            if position.underlying_direction == Direction.LONG:
                new_sl = position.highest_favorable_price - trail_distance
                if new_sl > sl:
                    return ManagementAction("MOVE_SL", new_stop_loss=new_sl)
            else:
                new_sl = position.highest_favorable_price + trail_distance
                if new_sl < sl:
                    return ManagementAction("MOVE_SL", new_stop_loss=new_sl)
        else:
            risk_unit = abs(position.entry_price - position.stop_loss)
            trail_distance = risk_unit * trade_mgmt_config.trailing_atr_multiplier
            if position.direction == Direction.LONG:
                new_sl = position.highest_favorable_price - trail_distance
                if new_sl > sl:
                    return ManagementAction("MOVE_SL", new_stop_loss=new_sl)
            else:
                new_sl = position.highest_favorable_price + trail_distance
                if new_sl < sl:
                    return ManagementAction("MOVE_SL", new_stop_loss=new_sl)

    return ManagementAction("HOLD")
