"""Shared data models used across strategy, risk, execution, and logging layers."""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class ExitReason(str, Enum):
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    TRAILING_STOP = "TRAILING_STOP"
    TIME_EXIT = "TIME_EXIT"
    MANUAL = "MANUAL"
    KILL_SWITCH = "KILL_SWITCH"
    DAILY_LIMIT = "DAILY_LIMIT"
    PARTIAL_BOOK = "PARTIAL_BOOK"


@dataclass
class Signal:
    """Output of a strategy's evaluation of one instrument at one point in time."""
    symbol: str
    timestamp: datetime
    direction: Direction
    strategy_name: str
    entry_price: float
    stop_loss: float
    take_profit: float
    confirmations: List[str] = field(default_factory=list)   # which sub-checks agreed
    win_probability: float = 0.5
    indicator_snapshot: Dict[str, float] = field(default_factory=dict)
    regime: Optional[str] = None

    @property
    def risk_per_share(self) -> float:
        return abs(self.entry_price - self.stop_loss)

    @property
    def reward_per_share(self) -> float:
        return abs(self.take_profit - self.entry_price)

    @property
    def risk_reward_ratio(self) -> float:
        if self.risk_per_share == 0:
            return 0.0
        return self.reward_per_share / self.risk_per_share


@dataclass
class Position:
    symbol: str
    direction: Direction
    entry_price: float
    quantity: int
    stop_loss: float
    take_profit: float
    entry_time: datetime
    strategy_name: str
    original_quantity: int = 0
    partial_booked: bool = False
    breakeven_applied: bool = False
    highest_favorable_price: float = 0.0
    order_id: Optional[str] = None
    underlying_symbol: Optional[str] = None
    underlying_entry_price: Optional[float] = None
    underlying_direction: Optional[Direction] = None
    underlying_stop_loss: Optional[float] = None
    underlying_take_profit: Optional[float] = None

    def __post_init__(self):
        if self.original_quantity == 0:
            self.original_quantity = self.quantity
        
        if self.underlying_symbol is not None:
            self.highest_favorable_price = self.underlying_entry_price
        else:
            self.highest_favorable_price = self.entry_price

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["direction"] = self.direction.value
        d["entry_time"] = self.entry_time.isoformat()
        if self.underlying_direction:
            d["underlying_direction"] = self.underlying_direction.value
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Position":
        data = data.copy()
        data["direction"] = Direction(data["direction"])
        data["entry_time"] = datetime.fromisoformat(data["entry_time"])
        if data.get("underlying_direction"):
            data["underlying_direction"] = Direction(data["underlying_direction"])
        return cls(**data)


@dataclass
class ClosedTrade:
    symbol: str
    direction: Direction
    entry_price: float
    exit_price: float
    quantity: int
    entry_time: datetime
    exit_time: datetime
    strategy_name: str
    exit_reason: ExitReason
    pnl: float
    pnl_pct: float
    entry_reason: str = ""
