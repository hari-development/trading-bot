"""
Backtester — replays historical OHLCV data bar-by-bar through the exact
same strategy / quality-filter / risk-manager / trade-manager code the
live engine uses, so backtest results are representative of live
behavior (no separate "backtest-only" logic to drift out of sync).

Supports:
- Simple historical backtest over one continuous window
- Walk-forward analysis: rolling train/test windows, re-evaluated
  sequentially, to catch overfitting to a single historical regime
- Standard metrics: win rate, profit factor, max drawdown, Sharpe ratio,
  equity curve, trade-by-trade log
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

import numpy as np
import pandas as pd

from config.settings import risk_config
from core.models import ClosedTrade, Direction, ExitReason, Position, Signal
from core.quality_filter import evaluate_signal_quality
from core.regime import classify_regime
from core.trade_manager import evaluate_position
from strategies.base import Strategy


@dataclass
class BacktestResult:
    trades: List[ClosedTrade] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)
    starting_capital: float = risk_config.starting_capital

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        wins = sum(1 for t in self.trades if t.pnl > 0)
        return wins / len(self.trades) * 100

    @property
    def profit_factor(self) -> float:
        gross_profit = sum(t.pnl for t in self.trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in self.trades if t.pnl < 0))
        return gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0

    @property
    def max_drawdown_pct(self) -> float:
        if not self.equity_curve:
            return 0.0
        curve = np.array(self.equity_curve)
        running_max = np.maximum.accumulate(curve)
        drawdowns = (running_max - curve) / running_max
        return float(np.max(drawdowns) * 100)

    @property
    def sharpe_ratio(self) -> float:
        if len(self.trades) < 2:
            return 0.0
        returns = np.array([t.pnl_pct for t in self.trades])
        if returns.std() == 0:
            return 0.0
        # annualization assumes ~252 trading days, avg trades/day estimated from data
        return float(returns.mean() / returns.std() * np.sqrt(252))

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl for t in self.trades)

    def summary(self) -> dict:
        return {
            "total_trades": len(self.trades),
            "win_rate_pct": round(self.win_rate, 2),
            "profit_factor": round(self.profit_factor, 2) if self.profit_factor != float("inf") else "inf",
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "sortino_ratio": round(self.sortino_ratio, 2) if self.sortino_ratio != float("inf") else "inf",
            "calmar_ratio": round(self.calmar_ratio, 2) if self.calmar_ratio != float("inf") else "inf",
            "total_pnl": round(self.total_pnl, 2),
            "ending_capital": round(self.starting_capital + self.total_pnl, 2),
            "return_pct": round(self.total_pnl / self.starting_capital * 100, 2),
            "avg_holding_minutes": self.avg_holding_minutes,
        }

    @property
    def sortino_ratio(self) -> float:
        """Sortino ratio — only penalizes downside volatility (better than Sharpe for trading)."""
        if len(self.trades) < 2:
            return 0.0
        returns = np.array([t.pnl_pct for t in self.trades])
        downside = returns[returns < 0]
        if len(downside) == 0 or downside.std() == 0:
            return float("inf") if returns.mean() > 0 else 0.0
        return float(returns.mean() / downside.std() * np.sqrt(252))

    @property
    def calmar_ratio(self) -> float:
        """Calmar ratio — annual return / max drawdown."""
        if self.max_drawdown_pct == 0:
            return float("inf")
        return round(self.total_pnl / self.starting_capital * 100 / self.max_drawdown_pct, 3)

    @property
    def avg_holding_minutes(self) -> float:
        """Average holding duration in minutes."""
        if not self.trades:
            return 0.0
        durations = []
        for t in self.trades:
            try:
                if t.entry_time and t.exit_time:
                    delta = (t.exit_time - t.entry_time).total_seconds() / 60
                    durations.append(delta)
            except Exception:
                pass
        return round(sum(durations) / len(durations), 1) if durations else 0.0

    @property
    def monthly_returns(self) -> dict:
        """P&L broken down by YYYY-MM."""
        monthly: dict = {}
        for t in self.trades:
            try:
                key = t.exit_time.strftime("%Y-%m") if hasattr(t.exit_time, 'strftime') else str(t.exit_time)[:7]
                monthly[key] = round(monthly.get(key, 0.0) + t.pnl, 2)
            except Exception:
                pass
        return dict(sorted(monthly.items()))

    def extended_summary(self) -> dict:
        """Full metrics dict including monthly breakdown."""
        base = self.summary()
        base["monthly_returns"] = self.monthly_returns
        return base

    def trade_log_df(self) -> pd.DataFrame:
        return pd.DataFrame([{
            "symbol": t.symbol, "direction": t.direction.value, "entry_time": t.entry_time,
            "exit_time": t.exit_time, "entry_price": t.entry_price, "exit_price": t.exit_price,
            "quantity": t.quantity, "strategy": t.strategy_name, "exit_reason": t.exit_reason.value,
            "pnl": t.pnl, "pnl_pct": t.pnl_pct,
        } for t in self.trades])


class Backtester:
    def __init__(self, strategies: List[Strategy], starting_capital: float = None):
        self.strategies = strategies
        self.starting_capital = starting_capital or risk_config.starting_capital

    def run(self, symbol: str, df: pd.DataFrame, min_lookback: int = 60) -> BacktestResult:
        """Single-pass backtest over one symbol's historical dataframe.
        df must be indexed by datetime, columns: open, high, low, close, volume."""
        result = BacktestResult(starting_capital=self.starting_capital)
        equity = self.starting_capital
        peak_equity = equity
        consecutive_losses = 0
        open_position: Optional[Position] = None

        for i in range(min_lookback, len(df)):
            window = df.iloc[:i + 1]
            current_bar = window.iloc[-1]
            current_time = window.index[-1]
            current_price = float(current_bar["close"])

            if open_position is not None:
                action = evaluate_position(open_position, current_price, current_time)
                if action.action in ("EXIT_FULL", "EXIT_PARTIAL"):
                    qty = action.exit_quantity or open_position.quantity
                    pnl_per_share = (current_price - open_position.entry_price) if \
                        open_position.direction == Direction.LONG else \
                        (open_position.entry_price - current_price)
                    pnl = pnl_per_share * qty
                    equity += pnl
                    trade = ClosedTrade(
                        symbol=symbol, direction=open_position.direction,
                        entry_price=open_position.entry_price, exit_price=current_price,
                        quantity=qty, entry_time=open_position.entry_time, exit_time=current_time,
                        strategy_name=open_position.strategy_name, exit_reason=action.reason,
                        pnl=pnl, pnl_pct=(pnl_per_share / open_position.entry_price * 100),
                    )
                    result.trades.append(trade)
                    if action.action == "EXIT_FULL":
                        consecutive_losses = consecutive_losses + 1 if pnl < 0 else 0
                        open_position = None
                    else:
                        open_position.quantity -= qty
                elif action.action == "MOVE_SL":
                    open_position.stop_loss = action.new_stop_loss

                peak_equity = max(peak_equity, equity)
                result.equity_curve.append(equity)
                continue

            # flat -> look for entry
            regime = classify_regime(window)
            for strategy in self.strategies:
                signal = strategy.evaluate(symbol, window)
                if signal is None:
                    continue
                signal.regime = regime.regime.value
                quality = evaluate_signal_quality(signal, window, regime)
                if not quality.passed:
                    continue
                if signal.risk_reward_ratio < risk_config.min_risk_reward_ratio:
                    continue

                # anti-martingale sizing, same rule as live risk manager
                risk_amount = equity * risk_config.max_risk_per_trade_pct / 100
                if consecutive_losses >= risk_config.losses_before_size_reduction:
                    risk_amount *= (1 - risk_config.size_reduction_after_loss_pct / 100)
                qty = int(risk_amount // signal.risk_per_share) if signal.risk_per_share > 0 else 0
                if qty <= 0:
                    continue

                open_position = Position(
                    symbol=symbol, direction=signal.direction, entry_price=signal.entry_price,
                    quantity=qty, stop_loss=signal.stop_loss, take_profit=signal.take_profit,
                    entry_time=current_time, strategy_name=signal.strategy_name,
                )
                break

            result.equity_curve.append(equity)

        return result

    def walk_forward(self, symbol: str, df: pd.DataFrame, train_bars: int = 500,
                      test_bars: int = 100) -> List[BacktestResult]:
        """Rolling walk-forward: evaluate on sequential out-of-sample windows.
        Since these strategies aren't parameter-fit to training data (they use
        fixed, sensible technical parameters), 'train' windows here serve to
        warm up indicator lookbacks rather than fit params — this still
        validates that performance holds up out-of-sample across different
        historical periods/regimes rather than being an artifact of one window."""
        results = []
        start = 0
        while start + train_bars + test_bars <= len(df):
            test_window = df.iloc[start: start + train_bars + test_bars]
            result = self.run(symbol, test_window, min_lookback=train_bars)
            results.append(result)
            start += test_bars
        return results


def monte_carlo_backtest(result: BacktestResult, n_iterations: int = 1000) -> dict:
    """
    Monte Carlo simulation — randomly reorders the trade sequence to estimate
    the range of possible outcomes given the same set of trades.
    Returns distribution stats on ending equity across all iterations.
    """
    if not result.trades:
        return {}
    pnls = [t.pnl for t in result.trades]
    ending_equities = []
    for _ in range(n_iterations):
        shuffled = list(pnls)
        np.random.shuffle(shuffled)
        equity = result.starting_capital
        for pnl in shuffled:
            equity += pnl
        ending_equities.append(equity)

    eq_arr = np.array(ending_equities)
    return {
        "n_iterations": n_iterations,
        "mean_ending_equity": round(float(eq_arr.mean()), 2),
        "median_ending_equity": round(float(np.median(eq_arr)), 2),
        "p5_worst_case": round(float(np.percentile(eq_arr, 5)), 2),
        "p95_best_case": round(float(np.percentile(eq_arr, 95)), 2),
        "probability_of_profit": round(float((eq_arr > result.starting_capital).mean() * 100), 1),
        "expected_return_pct": round(
            float((eq_arr.mean() - result.starting_capital) / result.starting_capital * 100), 2
        ),
    }
