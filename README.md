# Autonomous Indian Equities Trading Bot

A modular, risk-first algorithmic trading system for NSE equities. Paper-trading
works out of the box (yfinance data, no broker account needed). Live trading
runs through Zerodha Kite Connect.

**Status: core engine is real and tested (21/21 unit tests passing, verified
end-to-end backtest run). The Flutter dashboard is scaffolded but not yet
built — see "What's not built yet" below.**

## Why this architecture

Every module is deliberately decoupled so no single risk control can be
bypassed by adding a new strategy:

```
Signal (strategy)
   -> Quality Filter (core/quality_filter.py)     <- hard gate, rejects bad setups
   -> Risk Manager   (risk/risk_manager.py)         <- hard gate, sizing + circuit breakers
   -> Trade Manager   (core/trade_manager.py)        <- manages the position once open
   -> Broker           (execution/*.py)               <- only place orders actually get placed
```

The **same** quality filter, risk manager, and trade manager run in
`core/engine.py` (live loop) and `backtest/backtester.py` (historical replay).
There is no separate "backtest-only" logic — what you backtest is what runs live.

## Directory structure

```
config/settings.py       All tunables: risk limits, quality thresholds, trade
                          management params, enabled strategies, watchlist.
core/
  models.py               Signal / Position / ClosedTrade data models
  indicators.py            EMA, RSI, MACD, ATR, ADX, Bollinger, VWAP, SuperTrend, ORB
  regime.py                 Trending / Ranging / High-volatility classifier
  quality_filter.py          Rejects low-quality setups before risk sizing
  trade_manager.py            SL / TP / trailing / partial booking / breakeven / time exit
  engine.py                    The autonomous loop
strategies/
  base.py, ema_supertrend.py, vwap_breakout.py, rsi_macd_confluence.py,
  opening_range_breakout.py, registry.py (plug-in loader driven by config)
risk/
  risk_manager.py            Position sizing, daily limits, consecutive-loss
                               cooldown, drawdown hard-stop, kill switch
execution/
  broker_base.py, paper_broker.py (yfinance-backed), kite_broker.py, factory.py
backtest/backtester.py     Historical + walk-forward backtesting, metrics
utils/logger.py             Rotating logs + structured JSON trade-event log
tests/                        21 unit tests covering indicators, risk, trade mgmt
main.py                       Entry point
```

## How "intelligent loss recovery" actually works

This was the core requirement, so it's worth being explicit about where it
lives in code (`risk/risk_manager.py`):

1. **After every loss**, `consecutive_losses` increments. Position size for
   the *next* trade is cut by `size_reduction_after_loss_pct` (default 50%)
   once losses reach `losses_before_size_reduction` (default 2). It never
   scales up after a loss — there is no code path that does this.
2. **After `max_consecutive_losses`** (default 3), the bot enters a cooldown
   (`cooldown_after_max_losses_minutes`, default 60 min). No new entries
   during cooldown. When cooldown ends, market regime is re-classified from
   scratch (`core/regime.py`) — it does not assume the setup that failed is
   still valid.
3. **Full size is only restored** after `size_recovery_wins_required`
   (default 2) consecutive wins.
4. **Daily loss limit / daily profit lock / max drawdown** are hard circuit
   breakers independent of the above — breaching any of them halts new
   entries for the day (drawdown breach halts everything until you manually
   clear `state.hard_stop`, by design — that's meant to force a human look).
5. **Kill switch**: create the file `logs/KILL_SWITCH` (or call
   `risk_manager.emergency_stop()`) and the engine flattens all open
   positions and stops on the next cycle.

## Running it

### 1. Setup

```bash
cd trading_bot
python3 -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Paper trading (default, no broker account needed)

```bash
python main.py --mode PAPER
```

This pulls live NSE data via yfinance (free, 15-min delayed on the free tier
in practice, fine for validating logic before going live) and simulates
fills. Check `logs/trade_events.jsonl` for every entry/exit/rejection with
full reasoning, and `logs/engine.log` for the human-readable log.

### 3. Backtesting

```python
from backtest.backtester import Backtester
from strategies.registry import load_enabled_strategies
from execution.paper_broker import PaperBroker

broker = PaperBroker()
df = broker.get_historical_data("RELIANCE", "5minute", lookback_bars=2000)
bt = Backtester(load_enabled_strategies())
result = bt.run("RELIANCE", df)
print(result.summary())
result.trade_log_df().to_csv("backtest_results.csv")

# walk-forward, sequential out-of-sample windows:
for r in bt.walk_forward("RELIANCE", df, train_bars=500, test_bars=100):
    print(r.summary())
```

### 4. Going live (Zerodha Kite Connect)

**Do this only after real paper-trading validation over multiple weeks and
multiple market regimes. Nothing in this codebase should be trusted with
real capital on day one.**

1. Get API key + secret from <https://kite.trade> (Kite Connect subscription
   is ₹2000/month, separate from your regular Zerodha account).
2. Kite access tokens expire every day at market open — you need a daily
   login flow. Minimal example (`scripts/generate_token.py`, not included —
   build per Kite Connect's documented login flow):

   ```python
   from kiteconnect import KiteConnect
   kite = KiteConnect(api_key="your_key")
   print(kite.login_url())   # visit this, log in, copy request_token from redirect URL
   data = kite.generate_session("request_token_here", api_secret="your_secret")
   print(data["access_token"])
   ```

3. Set environment variables before starting the bot:

   ```bash
   export KITE_API_KEY="..."
   export KITE_API_SECRET="..."
   export KITE_ACCESS_TOKEN="..."   # refresh this daily, ideally via cron before market open
   ```

4. In `config/settings.py`, review every value in `RiskConfig` — the
   defaults are conservative starting points, not recommendations for your
   capital.
5. Run:

   ```bash
   python main.py --mode LIVE
   ```

   You'll be asked to type `CONFIRM LIVE` — this is intentional friction.

### 5. Emergency stop (live or paper)

```bash
touch logs/KILL_SWITCH
```

The bot checks for this file every cycle and flattens all positions
immediately when found. Delete the file to allow trading again.

### 6. Running as a background service on your laptop

Simplest option — `systemd` (Linux) or `launchd`/`pm2`/`screen` equivalent.
Quick and dirty with `nohup`:

```bash
nohup python main.py --mode PAPER > logs/stdout.log 2>&1 &
```

For anything closer to production, use `systemd` with `Restart=on-failure`
so a crash doesn't leave you flat-footed mid-session — happy to write that
unit file if/when you're ready to move off `nohup`.

## Testing

```bash
python -m pytest tests/ -v
```

21 tests currently cover indicator correctness, anti-martingale position
sizing, circuit breakers, cooldown logic, and trade management exits. Add
tests for any new strategy in `tests/` before enabling it in production.

## What's not built yet (honest scope of this delivery)

- **Flutter dashboard**: folder scaffolded at `dashboard_flutter/` but no
  code yet. The structured log at `logs/trade_events.jsonl` and
  `risk_manager.status_summary()` already contain everything the dashboard
  needs (live P&L, positions, win rate, drawdown, risk status) — next step
  is either a local REST/WebSocket wrapper around these, or having the
  Flutter app read the JSON logs directly for a v1. Given your Flutter
  background this is probably fastest built by you with me pairing on it —
  say the word and we'll do it next.
- **News/abnormal-market detection**: quality filter checks volatility,
  liquidity, and time-of-day, but there's no live news feed integration.
  Would need a news API (e.g. a paid NSE announcements feed) wired into
  `core/quality_filter.py`.
- **Bracket/cover order types, GTT orders**: `kite_broker.py` currently
  places plain MIS market orders; Kite's native bracket-order (SL baked
  into the exchange order) integration would reduce slippage risk vs.
  polling-based SL exits and is a natural next hardening step.
- Price Action and pure Volume Breakout strategies are listed in the
  original spec but not yet implemented as separate strategy classes —
  the existing four cover the mechanics (trend, breakout, momentum,
  mean-reversion); adding the remaining ones is mechanical work following
  the same `Strategy` interface.

## Deliberate non-negotiables baked into the code

- No martingale/doubling: verified by `tests/test_risk_manager.py::test_never_doubles_size_after_loss`.
- No trade bypasses the quality filter or risk manager — there's no
  second code path to `broker.place_order()` outside `engine._enter_trade()`.
- Max drawdown breach requires manual intervention (`state.hard_stop`),
  not an automatic resume — by design, this is not something the bot
  should self-clear.
