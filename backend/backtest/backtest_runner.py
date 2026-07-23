"""
Backtest Runner — vectorised bar-by-bar strategy replay.

Usage (from backend/ directory):
    python -m backtest.backtest_runner --symbol NIFTY --timeframe 5minute --days 365

Data source: yfinance (same as paper broker).
Output: console summary + JSON results file in logs/backtest_results.json

Metrics calculated:
  - Win Rate %
  - Profit Factor
  - Sharpe Ratio (annualised, assumes 252 trading days × 75 bars/day)
  - Max Drawdown %
  - Expectancy (avg P&L per trade in ₹)
  - Average Profit (winning trades)
  - Average Loss  (losing trades)
  - Total Trades
  - Total P&L
"""
import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

# Allow running as a module from the backend directory
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import trade_mgmt_config, risk_config
from core.indicators import atr
from core.models import Direction
from core.regime import classify_regime
from strategies.registry import load_enabled_strategies

try:
    import yfinance as yf
    _YF_AVAILABLE = True
except ImportError:
    _YF_AVAILABLE = False

_TIMEFRAME_MAP = {"1minute": "1m", "5minute": "5m", "15minute": "15m", "day": "1d"}
_YF_PERIOD_MAP = {"1m": "7d", "5m": "60d", "15m": "60d", "1d": "5y"}


def _fetch_data(symbol: str, timeframe: str = "5minute", days: int = 365) -> pd.DataFrame:
    if not _YF_AVAILABLE:
        raise RuntimeError("yfinance not installed. Run: pip install yfinance")
    yf_interval = _TIMEFRAME_MAP.get(timeframe, "5m")
    yf_symbol = symbol + ".NS"
    if symbol == "NIFTY":
        yf_symbol = "^NSEI"
    elif symbol == "BANKNIFTY":
        yf_symbol = "^NSEBANK"
    elif symbol == "SENSEX":
        yf_symbol = "^BSESN"
    period = _YF_PERIOD_MAP.get(yf_interval, "60d")
    ticker = yf.Ticker(yf_symbol)
    df = ticker.history(period=period, interval=yf_interval)
    if df.empty:
        raise ValueError(f"No data returned for {symbol}")
    df = df.rename(columns={"Open": "open", "High": "high", "Low": "low",
                             "Close": "close", "Volume": "volume"})
    df = df[["open", "high", "low", "close", "volume"]]
    df.index.name = "datetime"
    df["volume"] = df["volume"].fillna(0)
    return df


def _compute_metrics(trades: List[dict], equity_curve: List[float]) -> dict:
    if not trades:
        return {"error": "No trades to analyse"}

    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    total_profit = sum(wins)
    total_loss = abs(sum(losses))
    profit_factor = (total_profit / total_loss) if total_loss > 0 else float("inf")
    win_rate = len(wins) / len(pnls) * 100
    avg_profit = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(losses) / len(losses)) if losses else 0.0
    expectancy = (win_rate / 100) * avg_profit + (1 - win_rate / 100) * avg_loss

    # Sharpe Ratio (daily returns from equity curve)
    eq = np.array(equity_curve)
    if len(eq) > 1:
        daily_returns = np.diff(eq) / eq[:-1]
        sharpe = (daily_returns.mean() / daily_returns.std() * math.sqrt(252)
                  if daily_returns.std() > 0 else 0.0)
    else:
        sharpe = 0.0

    # Max Drawdown
    peak = eq[0]
    max_dd = 0.0
    for val in eq:
        peak = max(peak, val)
        dd = (peak - val) / peak * 100
        max_dd = max(max_dd, dd)

    return {
        "total_trades": len(pnls),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate_pct": round(win_rate, 2),
        "total_pnl": round(sum(pnls), 2),
        "profit_factor": round(profit_factor, 3),
        "sharpe_ratio": round(sharpe, 3),
        "max_drawdown_pct": round(max_dd, 2),
        "expectancy": round(expectancy, 2),
        "avg_profit": round(avg_profit, 2),
        "avg_loss": round(avg_loss, 2),
        "final_equity": round(eq[-1], 2),
    }


def run_backtest(
    symbol: str,
    timeframe: str = "5minute",
    days: int = 365,
    starting_capital: float = 25000.0,
) -> dict:
    print(f"\n{'='*60}")
    print(f" BACKTEST: {symbol} | {timeframe} | {days} days")
    print(f"{'='*60}")

    df = _fetch_data(symbol, timeframe, days)
    print(f" Data loaded: {len(df)} bars from {df.index[0].date()} to {df.index[-1].date()}")

    strategies = load_enabled_strategies()
    lookback = 60  # bars needed before first evaluation

    trades: List[dict] = []
    equity = starting_capital
    equity_curve = [equity]
    open_position: Optional[dict] = None

    for i in range(lookback, len(df)):
        window = df.iloc[:i]
        current_bar = df.iloc[i]
        current_close = float(current_bar["close"])

        # ── Manage open position ──────────────────────────────────────────
        if open_position is not None:
            direction = open_position["direction"]
            sl = open_position["stop_loss"]
            tp = open_position["take_profit"]
            entry = open_position["entry_price"]
            qty = open_position["quantity"]

            hit_sl = (direction == "LONG" and current_close <= sl) or \
                     (direction == "SHORT" and current_close >= sl)
            hit_tp = (direction == "LONG" and current_close >= tp) or \
                     (direction == "SHORT" and current_close <= tp)

            if hit_sl or hit_tp:
                exit_price = sl if hit_sl else tp
                pnl = (exit_price - entry) * qty if direction == "LONG" \
                    else (entry - exit_price) * qty
                equity += pnl
                equity_curve.append(equity)
                trades.append({
                    "symbol": symbol,
                    "direction": direction,
                    "entry_price": entry,
                    "exit_price": exit_price,
                    "quantity": qty,
                    "pnl": round(pnl, 2),
                    "exit_reason": "STOP_LOSS" if hit_sl else "TAKE_PROFIT",
                    "entry_time": open_position["entry_time"],
                    "exit_time": str(df.index[i]),
                })
                open_position = None
            continue  # only 1 position at a time

        # ── Scan for new entry ────────────────────────────────────────────
        regime = classify_regime(window)

        best_signal = None
        for strategy in strategies:
            try:
                sig = strategy.evaluate(symbol, window)
            except Exception:
                continue
            if sig is None:
                continue
            if best_signal is None or sig.win_probability > best_signal.win_probability:
                best_signal = sig

        if best_signal is None:
            continue

        # Simple risk-based sizing (no lot size enforcement in backtest)
        risk_amount = equity * risk_config.max_risk_per_trade_pct / 100
        risk_per_share = abs(best_signal.entry_price - best_signal.stop_loss)
        if risk_per_share <= 0:
            continue
        qty = max(1, int(risk_amount / risk_per_share))

        open_position = {
            "direction": best_signal.direction.value,
            "entry_price": float(best_signal.entry_price),
            "stop_loss": float(best_signal.stop_loss),
            "take_profit": float(best_signal.take_profit),
            "quantity": qty,
            "entry_time": str(df.index[i]),
        }

    # Close any still-open position at last bar
    if open_position is not None:
        last_price = float(df["close"].iloc[-1])
        direction = open_position["direction"]
        entry = open_position["entry_price"]
        qty = open_position["quantity"]
        pnl = (last_price - entry) * qty if direction == "LONG" else (entry - last_price) * qty
        equity += pnl
        equity_curve.append(equity)
        trades.append({
            "symbol": symbol,
            "direction": direction,
            "entry_price": entry,
            "exit_price": last_price,
            "quantity": qty,
            "pnl": round(pnl, 2),
            "exit_reason": "END_OF_DATA",
            "entry_time": open_position["entry_time"],
            "exit_time": str(df.index[-1]),
        })

    metrics = _compute_metrics(trades, equity_curve)
    results = {
        "symbol": symbol,
        "timeframe": timeframe,
        "days": days,
        "starting_capital": starting_capital,
        "run_at": datetime.now().isoformat(),
        "metrics": metrics,
        "trades": trades,
    }

    # Print summary
    print(f"\n {'─'*40}")
    for k, v in metrics.items():
        if k != "error":
            print(f"  {k:<25}: {v}")
    print(f" {'─'*40}\n")

    # Save results
    out_path = Path("logs/backtest_results.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2, default=str))
    print(f" Results saved to: {out_path.resolve()}\n")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run strategy backtest")
    parser.add_argument("--symbol", default="NIFTY", help="Symbol to backtest")
    parser.add_argument("--timeframe", default="5minute",
                        choices=["1minute", "5minute", "15minute", "day"])
    parser.add_argument("--days", type=int, default=365, help="Lookback days (max depends on yf)")
    parser.add_argument("--capital", type=float, default=25000.0, help="Starting capital in INR")
    args = parser.parse_args()
    run_backtest(args.symbol, args.timeframe, args.days, args.capital)
