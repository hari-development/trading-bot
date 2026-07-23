"""Shared data models used across strategy, risk, execution, and logging layers."""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


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
    indicator_snapshot: Dict[str, Any] = field(default_factory=dict)
    regime: Optional[str] = None
    # --- Phase 2 additions (AI + scoring layer) ---
    confidence_score: float = 0.0          # 0.0–1.0 weighted confidence score
    trade_score: int = 0                   # 0–100 trade quality score
    higher_tf_alignment: str = ""          # "ALIGNED" | "NEUTRAL" | "OPPOSING"

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


@dataclass
class TradeJournalEntry:
    """
    Extended trade record for adaptive learning and analytics.
    Captures full context at time of entry and exit.
    """
    # Core trade identifiers
    trade_id: str = ""
    symbol: str = ""
    direction: str = ""
    strategy_name: str = ""

    # Entry context
    entry_price: float = 0.0
    entry_time: str = ""
    entry_reason: str = ""
    confidence_score: float = 0.0
    trade_score: int = 0
    market_regime: str = ""
    higher_tf_alignment: str = ""
    indicator_snapshot: Dict[str, Any] = field(default_factory=dict)
    confirmations: List[str] = field(default_factory=list)

    # Position details
    quantity: int = 0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    risk_amount: float = 0.0
    risk_reward_ratio: float = 0.0

    # Exit context
    exit_price: float = 0.0
    exit_time: str = ""
    exit_reason: str = ""
    pnl: float = 0.0
    pnl_pct: float = 0.0
    holding_minutes: float = 0.0
    max_favorable_excursion: float = 0.0   # best price reached
    max_adverse_excursion: float = 0.0     # worst price reached

    @classmethod
    def from_closed_trade(cls, trade: "ClosedTrade", signal: Optional["Signal"] = None) -> "TradeJournalEntry":
        """Convenience factory to create from a ClosedTrade + original Signal."""
        import uuid
        duration = (trade.exit_time - trade.entry_time).total_seconds() / 60 if trade.entry_time and trade.exit_time else 0.0
        entry = cls(
            trade_id=str(uuid.uuid4())[:12],
            symbol=trade.symbol,
            direction=trade.direction.value if hasattr(trade.direction, 'value') else str(trade.direction),
            strategy_name=trade.strategy_name,
            entry_price=trade.entry_price,
            entry_time=trade.entry_time.isoformat() if trade.entry_time else "",
            exit_price=trade.exit_price,
            exit_time=trade.exit_time.isoformat() if trade.exit_time else "",
            exit_reason=trade.exit_reason.value if hasattr(trade.exit_reason, 'value') else str(trade.exit_reason),
            pnl=trade.pnl,
            pnl_pct=trade.pnl_pct,
            holding_minutes=duration,
            quantity=trade.quantity,
            entry_reason=trade.entry_reason,
        )
        if signal:
            entry.confidence_score = signal.confidence_score
            entry.trade_score = signal.trade_score
            entry.market_regime = signal.regime or ""
            entry.higher_tf_alignment = signal.higher_tf_alignment
            entry.indicator_snapshot = signal.indicator_snapshot
            entry.confirmations = signal.confirmations
            entry.risk_reward_ratio = signal.risk_reward_ratio
            entry.stop_loss = signal.stop_loss
            entry.take_profit = signal.take_profit
        return entry
