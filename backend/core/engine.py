"""
Trading Engine — the autonomous loop.

Flow per cycle, per watchlist symbol:
  1. Check global kill switch / risk gates (RiskManager.can_trade)
  2. Manage existing open positions first (SL/TP/trailing/partial/time-exit)
  3. If flat on this symbol and allowed to trade: fetch data →
     classify regime → MTF confirmation → run all enabled strategies →
     quality filter → size → enter.
  4. Log every decision, taken or rejected, with full reasoning.

No human intervention required after `engine.run()` is called.
"""
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from utils.market_hours import (
    is_market_open,
    next_open_description,
    seconds_until_market_open,
)

from config.settings import (
    LOT_SIZES,
    mtf_config,
    option_config,
    risk_config,
    strategy_config,
    system_config,
)
from core.models import ClosedTrade, Direction, ExitReason, Position, Signal
from core.quality_filter import evaluate_signal_quality
from core.regime import RegimeSnapshot, classify_regime
from core.trade_manager import evaluate_position
from execution.broker_base import Broker
from execution.factory import get_broker
from risk.risk_manager import RiskManager
from strategies.registry import load_enabled_strategies
from utils.dashboard_server import DashboardServer
from utils.logger import get_logger, log_trade_event

logger = get_logger("engine")

POSITIONS_FILE = Path("logs/open_positions.json")
CLOSED_TRADES_FILE = Path("logs/closed_trades.jsonl")


class TradingEngine:
    def __init__(self, broker: Optional[Broker] = None):
        self.broker = broker or get_broker()
        self.risk_manager = RiskManager()
        self.strategies = load_enabled_strategies()
        self.open_positions: Dict[str, Position] = self._load_positions()
        self.closed_trades: List[dict] = self._load_closed_trades()
        self._running = False

        logger.info(
            f"Engine initialized. Mode={system_config.mode}, "
            f"Strategies={[s.name for s in self.strategies]}, "
            f"Watchlist={system_config.watchlist}"
        )

        # Start local dashboard WebSocket server
        self.dashboard_server = DashboardServer(self)
        self.dashboard_server.start()

    # ---------------------------------------------------------------- persistence

    def _save_positions(self):
        try:
            POSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {k: pos.to_dict() for k, pos in self.open_positions.items()}
            POSITIONS_FILE.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.error(f"Failed to save open positions: {e}")

    def _load_positions(self) -> Dict[str, Position]:
        if POSITIONS_FILE.exists():
            try:
                data = json.loads(POSITIONS_FILE.read_text())
                return {k: Position.from_dict(v) for k, v in data.items()}
            except Exception as e:
                logger.error(f"Failed to load open positions: {e}")
        return {}

    def _load_closed_trades(self) -> List[dict]:
        """Load last N closed trades for the dashboard trade history panel."""
        trades = []
        if CLOSED_TRADES_FILE.exists():
            try:
                with open(CLOSED_TRADES_FILE) as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                trades.append(json.loads(line))
                            except Exception:
                                pass
            except Exception as e:
                logger.error(f"Failed to load closed trades: {e}")
        return trades[-50:]  # keep last 50 in memory

    def _append_closed_trade(self, trade_dict: dict):
        """Append a closed trade to the JSONL log and keep in-memory list."""
        try:
            CLOSED_TRADES_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(CLOSED_TRADES_FILE, "a") as f:
                f.write(json.dumps(trade_dict, default=str) + "\n")
            self.closed_trades.append(trade_dict)
            if len(self.closed_trades) > 50:
                self.closed_trades = self.closed_trades[-50:]
        except Exception as e:
            logger.error(f"Failed to append closed trade: {e}")

    # ---------------------------------------------------------------- loop

    def run(self):
        self._running = True
        logger.info("=== Trading engine started (autonomous mode) ===")

        _last_heartbeat: float = 0.0          # epoch seconds of last sleep heartbeat
        _HEARTBEAT_INTERVAL = 30 * 60         # log every 30 min while sleeping

        while self._running:
            # ----------------------------------------------------------------
            # Market-hours gate — sleep precisely until the next NSE session.
            # No data fetches, no cycles, no CPU waste while market is closed.
            # ----------------------------------------------------------------
            if not is_market_open():
                sleep_secs = seconds_until_market_open()
                now_epoch = time.time()

                if now_epoch - _last_heartbeat >= _HEARTBEAT_INTERVAL:
                    logger.info(
                        f"Market closed. Next session {next_open_description()}. "
                        f"Engine will auto-resume — no restart needed."
                    )
                    _last_heartbeat = now_epoch

                # Sleep in small chunks so Ctrl+C / kill-switch is still responsive.
                # Wake up at most every 60 seconds to re-check the market-open flag.
                chunk = min(sleep_secs, 60)
                time.sleep(max(chunk, 1))
                continue

            # Market is open — reset heartbeat timer so we log again next closure.
            _last_heartbeat = 0.0

            # ----------------------------------------------------------------
            # Normal trading cycle
            # ----------------------------------------------------------------
            try:
                self._cycle()
            except Exception as e:
                logger.exception(f"Unhandled error in trading cycle: {e}")
                log_trade_event("ERROR", {"error": str(e)})
            time.sleep(system_config.poll_interval_seconds)

    def stop(self):
        self._running = False
        logger.info("Engine stop requested.")
        if hasattr(self, "dashboard_server"):
            self.dashboard_server.stop()

    # -------------------------------------------------------------- cycle

    def _cycle(self):
        can_trade, gate_reason = self.risk_manager.can_trade()

        # Always manage open positions first — capital protection takes priority
        # over finding new entries, even when new entries are blocked.
        for symbol in list(self.open_positions.keys()):
            self._manage_position(symbol)

        logger.info(
            f"Scanning {len(system_config.watchlist)} symbols. "
            f"Open positions: {len(self.open_positions)}/{risk_config.max_open_positions}. "
            f"Allowed to trade: {can_trade} ({gate_reason})"
        )

        if not can_trade:
            if gate_reason == "KILL_SWITCH_ACTIVE":
                self._flatten_all_positions(ExitReason.KILL_SWITCH)
            return

        # Check concurrent position limit
        if len(self.open_positions) >= risk_config.max_open_positions:
            logger.info(
                f"Max open positions reached ({risk_config.max_open_positions}). "
                "Skipping entry scan."
            )
            return

        for symbol in system_config.watchlist:
            # Skip if we already have an open position in this underlying
            if symbol in self.open_positions or any(
                k.startswith(symbol + "_") for k in self.open_positions.keys()
            ):
                continue
            self._evaluate_entry(symbol)

    # -------------------------------------------------------------- MTF

    def _get_mtf_regimes(self, symbol: str) -> Dict[str, RegimeSnapshot]:
        """
        Fetch OHLCV bars for each MTF timeframe and classify the regime.
        Returns a dict of {timeframe: RegimeSnapshot}.
        Returns empty dict on any fetch failure.
        """
        regimes: Dict[str, RegimeSnapshot] = {}
        for tf in mtf_config.required_timeframes:
            try:
                df = self.broker.get_historical_data(
                    symbol, timeframe=tf, lookback_bars=100
                )
                if df is None or df.empty or len(df) < 20:
                    logger.warning(f"Insufficient MTF data for {symbol} @ {tf}")
                    return {}   # fail safe — if one timeframe fails, block entry
                regimes[tf] = classify_regime(df)
            except Exception as e:
                logger.warning(f"MTF fetch failed for {symbol} @ {tf}: {e}")
                return {}
        return regimes

    def _mtf_confirms_signal(
        self, signal: Signal, mtf_regimes: Dict[str, RegimeSnapshot]
    ) -> tuple[bool, str]:
        """
        Returns (True, '') when enough higher-timeframe regimes agree with
        the signal direction, (False, reason) otherwise.
        """
        if not mtf_config.enabled or not mtf_regimes:
            return True, ""

        from core.regime import Regime

        agreeing = 0
        for tf, snapshot in mtf_regimes.items():
            if signal.direction == Direction.LONG:
                if snapshot.regime == Regime.TRENDING_UP:
                    agreeing += 1
                elif snapshot.regime == Regime.TRENDING_DOWN:
                    # Hard veto — opposing strong trend on a higher TF
                    return False, f"mtf_opposing_downtrend_on_{tf}"
            else:  # SHORT
                if snapshot.regime == Regime.TRENDING_DOWN:
                    agreeing += 1
                elif snapshot.regime == Regime.TRENDING_UP:
                    return False, f"mtf_opposing_uptrend_on_{tf}"

        if agreeing < mtf_config.min_agreeing_timeframes:
            return (
                False,
                f"mtf_insufficient_agreement({agreeing}/{len(mtf_regimes)})",
            )
        return True, ""

    # -------------------------------------------------------------- entry

    def _evaluate_entry(self, symbol: str):
        try:
            df = self.broker.get_historical_data(
                symbol,
                timeframe=strategy_config.primary_timeframe,
                lookback_bars=strategy_config.lookback_bars,
            )
        except Exception as e:
            logger.error(f"Failed to fetch data for {symbol}: {e}")
            return
        if df is None or df.empty or len(df) < 30:
            return

        # Primary timeframe regime
        regime = classify_regime(df)

        # Multi-timeframe regimes (fetched once per symbol, shared across strategies)
        mtf_regimes = self._get_mtf_regimes(symbol) if mtf_config.enabled else {}

        best_signal: Optional[Signal] = None
        for strategy in self.strategies:
            try:
                signal = strategy.evaluate(symbol, df)
            except Exception as e:
                logger.error(f"Strategy {strategy.name} raised an error on {symbol}: {e}")
                continue
            if signal is None:
                continue
            signal.regime = regime.regime.value

            # Quality filter (news, time, confirmations, ATR, volume, win-prob, regime)
            quality = evaluate_signal_quality(signal, df, regime)
            if not quality.passed:
                log_trade_event("SIGNAL_REJECTED", {
                    "symbol": symbol,
                    "strategy": strategy.name,
                    "reason": quality.reason,
                    "regime": regime.regime.value,
                })
                continue

            # Multi-timeframe confirmation gate
            mtf_ok, mtf_reason = self._mtf_confirms_signal(signal, mtf_regimes)
            if not mtf_ok:
                log_trade_event("SIGNAL_REJECTED", {
                    "symbol": symbol,
                    "strategy": strategy.name,
                    "reason": mtf_reason,
                    "regime": regime.regime.value,
                })
                logger.info(f"MTF blocked {strategy.name} on {symbol}: {mtf_reason}")
                continue

            if not self.risk_manager.validate_risk_reward(signal):
                log_trade_event("SIGNAL_REJECTED", {
                    "symbol": symbol,
                    "strategy": strategy.name,
                    "reason": "below_min_risk_reward",
                })
                continue

            # Prefer the highest win-probability signal when multiple strategies fire
            if best_signal is None or signal.win_probability > best_signal.win_probability:
                best_signal = signal

        if best_signal is None:
            return

        self._enter_trade(best_signal, regime)

    def _enter_trade(self, signal: Signal, regime: RegimeSnapshot):
        if option_config.enabled:
            option_type = "CE" if signal.direction == Direction.LONG else "PE"
            try:
                contract = self.broker.get_option_contract(
                    signal.symbol, option_type, signal.entry_price,
                    strike_selection=getattr(option_config, "strike_selection", "ITM1")
                )
                trade_symbol = contract["tradingsymbol"]
                option_price = self.broker.get_ltp(trade_symbol)
            except Exception as e:
                logger.error(
                    f"Failed to resolve option contract or fetch LTP for {signal.symbol}: {e}"
                )
                log_trade_event(
                    "ERROR",
                    {"symbol": signal.symbol, "error": f"option_resolution_failed: {e}"},
                )
                return

            # Risk-based position sizing on option premium
            current_equity = self.risk_manager.state.current_equity
            risk_amount = current_equity * risk_config.max_risk_per_trade_pct / 100.0
            option_risk_per_share = option_price * option_config.sl_pct / 100.0

            # Use centralised lot sizes; default to 1 if symbol not mapped
            lot_size = 1
            for key, size in LOT_SIZES.items():
                if key in signal.symbol:
                    lot_size = size
                    break

            lots = max(1, round(risk_amount / (option_risk_per_share * lot_size)))
            qty = lots * lot_size

            trade_direction = Direction.LONG  # option buying is always long premium
            trade_entry = option_price
            trade_sl = round(option_price * (1.0 - option_config.sl_pct / 100.0), 2)
            trade_tp = round(option_price * (1.0 + option_config.tp_pct / 100.0), 2)
        else:
            qty = self.risk_manager.calculate_position_size(signal, regime)
            if qty <= 0:
                log_trade_event("SIGNAL_REJECTED", {
                    "symbol": signal.symbol,
                    "strategy": signal.strategy_name,
                    "reason": "position_size_zero_after_risk_adjustment",
                })
                return

            trade_symbol = signal.symbol
            trade_direction = signal.direction
            trade_entry = signal.entry_price
            trade_sl = signal.stop_loss
            trade_tp = signal.take_profit

        try:
            order_id = self.broker.place_order(trade_symbol, trade_direction, qty)
        except Exception as e:
            logger.error(f"Order placement failed for {trade_symbol}: {e}")
            log_trade_event("ERROR", {"symbol": trade_symbol, "error": str(e)})
            return

        if option_config.enabled:
            position = Position(
                symbol=trade_symbol,
                direction=trade_direction,
                entry_price=trade_entry,
                quantity=qty,
                stop_loss=trade_sl,
                take_profit=trade_tp,
                entry_time=signal.timestamp,
                strategy_name=signal.strategy_name,
                order_id=order_id,
                underlying_symbol=signal.symbol,
                underlying_entry_price=signal.entry_price,
                underlying_direction=signal.direction,
                underlying_stop_loss=signal.stop_loss,
                underlying_take_profit=signal.take_profit,
            )
        else:
            position = Position(
                symbol=trade_symbol,
                direction=trade_direction,
                entry_price=trade_entry,
                quantity=qty,
                stop_loss=trade_sl,
                take_profit=trade_tp,
                entry_time=signal.timestamp,
                strategy_name=signal.strategy_name,
                order_id=order_id,
            )

        self.open_positions[trade_symbol] = position
        self._save_positions()

        log_trade_event("ENTRY", {
            "symbol": trade_symbol,
            "strategy": signal.strategy_name,
            "direction": trade_direction.value,
            "quantity": qty,
            "entry_price": trade_entry,
            "stop_loss": trade_sl,
            "take_profit": trade_tp,
            "risk_reward": round(signal.risk_reward_ratio, 2),
            "win_probability": signal.win_probability,
            "confirmations": signal.confirmations,
            "regime": signal.regime,
            "indicators": signal.indicator_snapshot,
            "entry_reason": (
                f"{signal.strategy_name} fired with {len(signal.confirmations)} confirmations "
                f"in {signal.regime} regime: {', '.join(signal.confirmations)}"
            ),
        })
        logger.info(
            f"ENTERED {trade_direction.value} {qty} {trade_symbol} @ {trade_entry:.2f} "
            f"[{signal.strategy_name}] SL={trade_sl:.2f} TP={trade_tp:.2f}"
        )

    # ------------------------------------------------------------ manage

    def _manage_position(self, symbol: str):
        position = self.open_positions[symbol]

        # For option positions: check SL/TP on the underlying instrument so we
        # don't get whipsawed by option premium noise (bid-ask spread, IV decay).
        # The option's own LTP is used only for P&L display.
        try:
            if position.underlying_symbol is not None:
                current_price = self.broker.get_ltp(position.underlying_symbol)
            else:
                current_price = self.broker.get_ltp(symbol)
        except Exception as e:
            logger.error(f"Failed to fetch LTP for {symbol}: {e}")
            return

        use_underlying = position.underlying_symbol is not None
        sl = position.underlying_stop_loss if use_underlying else position.stop_loss
        tp = position.underlying_take_profit if use_underlying else position.take_profit
        logger.info(
            f"Checking {symbol}: Current Price={current_price:.2f}, "
            f"Target TP={tp:.2f}, Target SL={sl:.2f} "
            f"(tracking underlying: {use_underlying})"
        )

        # Premium fail-safe & Trailing Option Premium Stop-Loss
        if use_underlying:
            try:
                option_ltp = self.broker.get_ltp(symbol)
                
                # Trailing option premium SL: trail option SL once premium turns profitable
                if getattr(option_config, "trail_premium_sl", True):
                    gain_pct = ((option_ltp - position.entry_price) / position.entry_price) * 100.0 if position.entry_price > 0 else 0.0
                    activation_pct = getattr(option_config, "premium_trail_activation_pct", 15.0)
                    trail_pct = getattr(option_config, "premium_trail_pct", 10.0)
                    
                    if gain_pct >= activation_pct:
                        new_sl = round(option_ltp * (1.0 - trail_pct / 100.0), 2)
                        if new_sl > position.stop_loss:
                            old_sl = position.stop_loss
                            position.stop_loss = new_sl
                            logger.info(
                                f"{symbol}: Option premium trailing stop moved {old_sl:.2f} → {position.stop_loss:.2f} "
                                f"(Peak Premium: ₹{option_ltp:.2f})"
                            )
                            self._save_positions()

                if option_ltp <= position.stop_loss:
                    exit_reason = ExitReason.TRAILING_STOP if position.stop_loss > (position.entry_price * (1.0 - option_config.sl_pct / 100.0)) else ExitReason.STOP_LOSS
                    logger.warning(
                        f"{symbol} premium ({option_ltp:.2f}) hit stop-loss target ({position.stop_loss:.2f}). Exiting immediately."
                    )
                    self._exit_position(
                        position, option_ltp, exit_reason, position.quantity, partial=False
                    )
                    return
                if option_ltp >= position.take_profit:
                    logger.info(
                        f"{symbol} premium ({option_ltp:.2f}) hit take-profit target ({position.take_profit:.2f}). Exiting immediately."
                    )
                    self._exit_position(
                        position, option_ltp, ExitReason.TAKE_PROFIT, position.quantity, partial=False
                    )
                    return
            except Exception as e:
                logger.error(f"Failed to run premium fail-safe check for {symbol}: {e}")

        action = evaluate_position(position, current_price, datetime.now())

        if action.action == "HOLD":
            return

        if action.action == "MOVE_SL":
            if position.underlying_symbol is not None:
                old_sl = position.underlying_stop_loss
                position.underlying_stop_loss = action.new_stop_loss
                logger.info(
                    f"{symbol}: underlying stop-loss moved "
                    f"{old_sl:.2f} → {position.underlying_stop_loss:.2f}"
                )
            else:
                old_sl = position.stop_loss
                position.stop_loss = action.new_stop_loss
                logger.info(
                    f"{symbol}: stop-loss moved {old_sl:.2f} → {position.stop_loss:.2f}"
                )
            log_trade_event("RISK_EVENT", {
                "symbol": symbol,
                "event": "sl_adjusted",
                "old_sl": old_sl,
                "new_sl": action.new_stop_loss,
            })
            self._save_positions()

            # Immediate breach check after moving stop-loss: exit then and there if breached
            eval_dir = position.underlying_direction if position.underlying_symbol else position.direction
            check_sl = position.underlying_stop_loss if position.underlying_symbol else position.stop_loss
            is_breached = (eval_dir == Direction.LONG and current_price <= check_sl) or \
                          (eval_dir == Direction.SHORT and current_price >= check_sl)
            if is_breached:
                logger.warning(
                    f"{symbol}: current price ({current_price:.2f}) immediately breached "
                    f"updated stop-loss ({check_sl:.2f}). Exiting immediately then and there."
                )
                try:
                    exit_price = self.broker.get_ltp(symbol)
                except Exception:
                    exit_price = position.entry_price
                reason = ExitReason.TRAILING_STOP if (position.partial_booked or position.breakeven_applied) else ExitReason.STOP_LOSS
                self._exit_position(position, exit_price, reason, position.quantity, partial=False)
            return

        # For EXIT actions, get the option's own current price for actual P&L
        if action.action in ("EXIT_PARTIAL", "EXIT_FULL"):
            try:
                exit_price = self.broker.get_ltp(symbol)
            except Exception:
                exit_price = position.entry_price

            if action.action == "EXIT_PARTIAL":
                self._exit_position(
                    position, exit_price, action.reason, action.exit_quantity, partial=True
                )
            else:
                self._exit_position(
                    position, exit_price, action.reason, position.quantity, partial=False
                )

    def _exit_position(
        self,
        position: Position,
        exit_price: float,
        reason: ExitReason,
        quantity: int,
        partial: bool,
    ):
        opposite = Direction.SHORT if position.direction == Direction.LONG else Direction.LONG
        try:
            self.broker.place_order(position.symbol, opposite, quantity)
        except Exception as e:
            logger.error(f"Exit order failed for {position.symbol}: {e}")
            return

        # P&L is always calculated on the actual instrument price (option premium for options)
        pnl_per_share = (
            (exit_price - position.entry_price)
            if position.direction == Direction.LONG
            else (position.entry_price - exit_price)
        )
        pnl = pnl_per_share * quantity
        pnl_pct = (pnl_per_share / position.entry_price) * 100 if position.entry_price else 0.0

        closed_trade = ClosedTrade(
            symbol=position.symbol,
            direction=position.direction,
            entry_price=position.entry_price,
            exit_price=exit_price,
            quantity=quantity,
            entry_time=position.entry_time,
            exit_time=datetime.now(),
            strategy_name=position.strategy_name,
            exit_reason=reason,
            pnl=pnl,
            pnl_pct=pnl_pct,
        )

        trade_dict = {
            "symbol": position.symbol,
            "direction": position.direction.value,
            "strategy": position.strategy_name,
            "entry_price": position.entry_price,
            "exit_price": exit_price,
            "quantity": quantity,
            "entry_time": position.entry_time.isoformat(),
            "exit_time": datetime.now().isoformat(),
            "exit_reason": reason.value,
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "partial": partial,
        }

        # Only feed risk manager's streak logic on full exits
        if not partial:
            self.risk_manager.register_closed_trade(closed_trade)
            del self.open_positions[position.symbol]
            self._append_closed_trade(trade_dict)
        else:
            position.quantity -= quantity
            self.risk_manager.state.current_equity += pnl
            self.risk_manager.state.realized_pnl_today += pnl
            self.risk_manager._persist()

        log_trade_event("EXIT", {
            "symbol": position.symbol,
            "strategy": position.strategy_name,
            "exit_reason": reason.value,
            "exit_price": exit_price,
            "quantity": quantity,
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "partial": partial,
        })
        logger.info(
            f"EXIT ({'partial' if partial else 'full'}) {position.symbol} "
            f"@ {exit_price:.2f} reason={reason.value} P&L={pnl:.2f}"
        )
        self._save_positions()

    def _flatten_all_positions(self, reason: ExitReason):
        for symbol in list(self.open_positions.keys()):
            position = self.open_positions[symbol]
            try:
                price = self.broker.get_ltp(symbol)
            except Exception:
                price = position.entry_price
            self._exit_position(position, price, reason, position.quantity, partial=False)
