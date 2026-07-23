"""
Trade Quality Filter — Phase 6: Trade Quality Score (0-100).

Every Signal passes through two layers:
  LAYER 1 — Binary Gates (fail-fast, in priority order):
    1. News / macro-event blackout
    2. Trading window (time-of-day)
    3. Risk-reward sanity
    4. ATR% (volatility blowout)
    5. Liquidity / volume (stocks only)
    6. Regime safety (HIGH_VOLATILITY, NEWS_DRIVEN blocked)
    7. Regime-direction conflict (trend-following vs opposing trend)

  LAYER 2 — Trade Score (0-100):
    Scores the signal quality across 6 weighted dimensions.
    Signals below min_trade_score (default 60) are rejected.

Reasons for rejection are always logged.
"""
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Dict, Optional, Tuple

import pandas as pd

from config.settings import quality_config, trade_score_config
from core.indicators import avg_volume, atr, adx as adx_fn, rsi
from core.models import Direction, Signal
from core.news_filter import is_news_blackout
from core.regime import Regime, RegimeSnapshot


@dataclass
class FilterResult:
    passed: bool
    reason: str = ""
    trade_score: int = 0        # 0-100 — populated even on rejection for analytics


def _within_trading_window(now: datetime) -> Tuple[bool, str]:
    open_t = time(9, 15)
    close_t = time(15, 30)
    avoid_open_until = (
        datetime.combine(now.date(), open_t)
        + pd.Timedelta(minutes=quality_config.avoid_first_minutes_after_open)
    ).time()
    avoid_close_after = (
        datetime.combine(now.date(), close_t)
        - pd.Timedelta(minutes=quality_config.avoid_last_minutes_before_close)
    ).time()
    t = now.time()
    if t < open_t or t > close_t:
        return False, "outside_market_hours"
    if t < avoid_open_until:
        return False, "within_opening_whipsaw_window"
    if t > avoid_close_after:
        return False, "within_closing_volatility_window"
    return True, ""


def score_signal(signal: Signal, df: pd.DataFrame, regime: RegimeSnapshot,
                 htf_regimes: Optional[Dict] = None) -> int:
    """
    Scores a signal on a 0-100 scale across 6 dimensions.
    Higher = better quality trade setup.

    Scoring breakdown:
      Regime suitability:    0-15 pts
      Trend quality:         0-20 pts (ADX + EMA alignment)
      Momentum:              0-20 pts (RSI + MACD from signal's confirmation list)
      Volume:                0-15 pts
      HTF alignment:         0-15 pts
      Confirmation count:    0-15 pts
    """
    score = 0
    cfg = trade_score_config

    # 1. REGIME SUITABILITY (0-15)
    regime_score = {
        Regime.TRENDING_UP:    cfg.regime_max,
        Regime.TRENDING_DOWN:  cfg.regime_max,
        Regime.RANGING:        int(cfg.regime_max * 0.5),
        Regime.GAP_UP:         int(cfg.regime_max * 0.8),
        Regime.GAP_DOWN:       int(cfg.regime_max * 0.8),
        Regime.LOW_VOLATILITY: int(cfg.regime_max * 0.3),
        Regime.HIGH_VOLATILITY: 0,
        Regime.NEWS_DRIVEN:    0,
        Regime.UNKNOWN:        int(cfg.regime_max * 0.4),
    }.get(regime.regime, int(cfg.regime_max * 0.4))

    # Penalty if direction opposes regime direction
    if regime.regime == Regime.TRENDING_UP and signal.direction == Direction.SHORT:
        regime_score = int(regime_score * 0.3)
    elif regime.regime == Regime.TRENDING_DOWN and signal.direction == Direction.LONG:
        regime_score = int(regime_score * 0.3)

    score += regime_score

    # 2. TREND QUALITY — ADX strength (0-20)
    trend_score = 0
    try:
        adx_series, plus_di, minus_di = adx_fn(df, 14)
        last_adx = float(adx_series.iloc[-1])
        if last_adx >= 30:
            trend_score = cfg.trend_quality_max
        elif last_adx >= 22:
            trend_score = int(cfg.trend_quality_max * 0.7)
        elif last_adx >= 15:
            trend_score = int(cfg.trend_quality_max * 0.4)

        # Bonus if ADX DI aligns with direction
        last_plus = float(plus_di.iloc[-1])
        last_minus = float(minus_di.iloc[-1])
        di_aligned = (signal.direction == Direction.LONG and last_plus > last_minus) or \
                     (signal.direction == Direction.SHORT and last_minus > last_plus)
        if di_aligned and trend_score > 0:
            trend_score = min(cfg.trend_quality_max, int(trend_score * 1.2))
    except Exception:
        pass
    score += trend_score

    # 3. MOMENTUM — RSI position (0-20)
    momentum_score = 0
    try:
        rsi_series = rsi(df["close"], 14)
        last_rsi = float(rsi_series.iloc[-1])
        if signal.direction == Direction.LONG:
            if 35 <= last_rsi <= 55:    # recovering from oversold
                momentum_score = cfg.momentum_max
            elif 55 < last_rsi <= 65:   # bullish momentum
                momentum_score = int(cfg.momentum_max * 0.7)
            elif last_rsi > 70:         # overbought risk
                momentum_score = int(cfg.momentum_max * 0.2)
            else:
                momentum_score = int(cfg.momentum_max * 0.5)
        else:  # SHORT
            if 45 <= last_rsi <= 65:
                momentum_score = cfg.momentum_max
            elif 35 <= last_rsi < 45:
                momentum_score = int(cfg.momentum_max * 0.7)
            elif last_rsi < 30:
                momentum_score = int(cfg.momentum_max * 0.2)
            else:
                momentum_score = int(cfg.momentum_max * 0.5)
        # Bonus for MACD in signal confirmations
        if "macd" in signal.confirmations:
            momentum_score = min(cfg.momentum_max, int(momentum_score * 1.15))
    except Exception:
        pass
    score += momentum_score

    # 4. VOLUME (0-15)
    volume_score = 0
    try:
        from core.indicators import avg_volume as avg_vol_fn
        vol_avg = avg_vol_fn(df, 20).iloc[-1]
        last_vol = float(df["volume"].iloc[-1])
        if not pd.isna(vol_avg) and vol_avg > 0:
            ratio = last_vol / vol_avg
            if ratio >= 2.0:
                volume_score = cfg.volume_max
            elif ratio >= 1.5:
                volume_score = int(cfg.volume_max * 0.8)
            elif ratio >= 1.2:
                volume_score = int(cfg.volume_max * 0.6)
            elif ratio >= 1.0:
                volume_score = int(cfg.volume_max * 0.3)
    except Exception:
        pass
    score += volume_score

    # 5. HTF ALIGNMENT (0-15)
    htf_score = 0
    if htf_regimes:
        from core.regime import multi_tf_regime_consensus
        consensus = multi_tf_regime_consensus(htf_regimes)
        htf_bias = consensus.get("htf_bias", "NEUTRAL")
        agreement = consensus.get("agreement_score", 0.0)
        if htf_bias == "BULLISH" and signal.direction == Direction.LONG:
            htf_score = int(cfg.htf_alignment_max * agreement)
        elif htf_bias == "BEARISH" and signal.direction == Direction.SHORT:
            htf_score = int(cfg.htf_alignment_max * agreement)
        elif htf_bias == "NEUTRAL":
            htf_score = int(cfg.htf_alignment_max * 0.4)
        # Penalty for opposing HTF
        elif (htf_bias == "BEARISH" and signal.direction == Direction.LONG) or \
             (htf_bias == "BULLISH" and signal.direction == Direction.SHORT):
            htf_score = 0
    else:
        # No HTF data — neutral, give partial credit
        htf_score = int(cfg.htf_alignment_max * 0.4)
        if signal.higher_tf_alignment == "ALIGNED":
            htf_score = cfg.htf_alignment_max
        elif signal.higher_tf_alignment == "OPPOSING":
            htf_score = 0
    score += htf_score

    # 6. CONFIRMATION COUNT (0-15)
    n_conf = len(signal.confirmations)
    if n_conf >= 6:
        conf_score = cfg.confirmation_count_max
    elif n_conf >= 5:
        conf_score = int(cfg.confirmation_count_max * 0.85)
    elif n_conf >= 4:
        conf_score = int(cfg.confirmation_count_max * 0.70)
    elif n_conf >= 3:
        conf_score = int(cfg.confirmation_count_max * 0.55)
    elif n_conf >= 2:
        conf_score = int(cfg.confirmation_count_max * 0.35)
    else:
        conf_score = 0
    score += conf_score

    # Bonus: confidence score from strategy (scaled to +5 max)
    if signal.confidence_score > 0:
        score += min(5, int(signal.confidence_score * 5))

    return min(100, max(0, score))


def evaluate_signal_quality(
    signal: Signal, df: pd.DataFrame, regime: RegimeSnapshot,
    htf_regimes: Optional[Dict] = None,
) -> FilterResult:
    """
    Two-layer quality gate. Returns FilterResult with passed=True only when
    all binary gates pass AND the trade score meets the minimum threshold.
    """
    # ── LAYER 1: Binary Gates ──────────────────────────────────────────────

    # 1. News blackout
    if quality_config.enable_news_filter:
        blackout, event_label = is_news_blackout(signal.timestamp)
        if blackout:
            return FilterResult(False, f"news_blackout({event_label})")

    # 2. Trading window
    ok, reason = _within_trading_window(signal.timestamp)
    if not ok:
        return FilterResult(False, reason)

    # 3. Risk-reward sanity
    if signal.risk_reward_ratio < 1e-9:
        return FilterResult(False, "invalid_risk_reward")
    if signal.risk_reward_ratio < 1.0:
        return FilterResult(False, f"risk_reward_too_low({signal.risk_reward_ratio:.2f})")

    # 4. Volatility blowout check
    last_atr = atr(df, 14).iloc[-1]
    last_close = df["close"].iloc[-1]
    if last_close:
        atr_pct = (last_atr / last_close) * 100
        if atr_pct > quality_config.max_atr_pct_of_price:
            return FilterResult(False, f"volatility_too_high(atr_pct={atr_pct:.2f})")

    # 5. Liquidity check (skip for indices)
    is_index = (
        signal.symbol in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX")
        or "NIFTY" in signal.symbol
        or "SENSEX" in signal.symbol
    )
    if not is_index:
        vol_avg = avg_volume(df, 20).iloc[-1]
        if pd.isna(vol_avg) or vol_avg < quality_config.min_avg_volume:
            return FilterResult(False, f"liquidity_too_low(avg_vol={vol_avg:.0f})")

    # 6. Regime safety — block dangerous regimes
    if regime.regime == Regime.HIGH_VOLATILITY:
        return FilterResult(False, "regime_high_volatility_lockout")
    if regime.regime == Regime.NEWS_DRIVEN:
        return FilterResult(False, "regime_news_driven_lockout")

    # 7. Regime-direction conflict (trend-following strategies only)
    trend_following = {"ema_supertrend", "vwap_breakout", "opening_range_breakout"}
    if signal.strategy_name in trend_following:
        if regime.regime == Regime.TRENDING_DOWN and signal.direction == Direction.LONG:
            return FilterResult(False, "signal_against_dominant_downtrend")
        if regime.regime == Regime.TRENDING_UP and signal.direction == Direction.SHORT:
            return FilterResult(False, "signal_against_dominant_uptrend")

    # ── LAYER 2: Trade Score (0-100) ──────────────────────────────────────
    ts = score_signal(signal, df, regime, htf_regimes)
    signal.trade_score = ts  # write score back to signal for dashboard display

    if ts < quality_config.min_trade_score:
        return FilterResult(False, f"trade_score_too_low({ts}/100)", trade_score=ts)

    return FilterResult(True, f"all_checks_passed(score={ts})", trade_score=ts)

