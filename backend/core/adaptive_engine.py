"""
Adaptive Learning Engine — Phase 9: Adaptive Strategy Weighting.

Tracks strategy performance per market regime and adjusts strategy
selection weights dynamically over time.

Data structure:
  {
    strategy_name: {
      regime: {
        wins: int,
        losses: int,
        total_pnl: float,
        avg_confidence: float,
        sample_count: int,
      }
    }
  }

Minimum sample_count before weight adjustment activates (prevents overfitting
to sparse data). Weight decay applies to older data.

Usage:
    engine = AdaptiveEngine()
    engine.record_trade("ema_supertrend", "TRENDING_UP", pnl=120.5, confidence=0.72)
    weights = engine.get_strategy_weights("TRENDING_UP")
    # returns {"ema_supertrend": 0.45, "vwap_breakout": 0.30, ...}
"""
import json
import threading
from pathlib import Path
from typing import Dict, List, Optional

from config.settings import adaptive_config
from utils.logger import get_logger

logger = get_logger("adaptive_engine")

_DEFAULT_WEIGHT = 1.0  # equal weight before data accumulates
_MIN_WEIGHT = 0.1       # never go below this (keep strategy in rotation)
_MAX_WEIGHT = 3.0       # cap weight to prevent single-strategy domination


class AdaptiveEngine:
    """
    Thread-safe adaptive learning engine.
    Records trade outcomes and computes strategy weights per regime.
    """

    def __init__(self, persistence_file: Optional[str] = None):
        self._lock = threading.RLock()
        self._data: Dict[str, Dict[str, dict]] = {}
        self._persistence_path = Path(
            persistence_file or adaptive_config.persistence_file
        )
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_trade(
        self,
        strategy_name: str,
        regime: str,
        pnl: float,
        confidence: float = 0.0,
        duration_minutes: float = 0.0,
    ) -> None:
        """Record a completed trade's outcome for adaptive learning."""
        with self._lock:
            if strategy_name not in self._data:
                self._data[strategy_name] = {}
            if regime not in self._data[strategy_name]:
                self._data[strategy_name][regime] = {
                    "wins": 0, "losses": 0, "total_pnl": 0.0,
                    "avg_confidence": 0.0, "sample_count": 0,
                }
            s = self._data[strategy_name][regime]
            # Update with exponential decay (older trades count less)
            decay = adaptive_config.weight_decay_factor
            s["wins"] = int(s["wins"] * decay) + (1 if pnl > 0 else 0)
            s["losses"] = int(s["losses"] * decay) + (1 if pnl <= 0 else 0)
            s["total_pnl"] = s["total_pnl"] * decay + pnl
            s["avg_confidence"] = (s["avg_confidence"] * decay + confidence) / (decay + 1)
            s["sample_count"] += 1

        self._save()
        logger.debug(
            f"Adaptive: recorded {strategy_name}/{regime} pnl={pnl:.1f} "
            f"confidence={confidence:.2f}"
        )

    def get_strategy_weights(
        self, regime: str, strategy_names: Optional[List[str]] = None
    ) -> Dict[str, float]:
        """
        Returns normalized weights for each strategy given the current regime.
        Strategies without enough data get equal default weight.

        Args:
            regime: current market regime string
            strategy_names: list of strategies to weight. If None, uses all known strategies.

        Returns:
            Dict mapping strategy_name -> weight (normalized, sum approximately equal to N)
        """
        with self._lock:
            names = strategy_names or list(self._data.keys())
            if not names:
                return {}

            raw_weights: Dict[str, float] = {}
            for name in names:
                regime_data = self._data.get(name, {}).get(regime, {})
                raw_weights[name] = self._compute_weight(regime_data)

            # Normalize so the best strategy gets weight=1.0 and others are relative
            max_w = max(raw_weights.values()) if raw_weights else 1.0
            if max_w == 0:
                return {n: _DEFAULT_WEIGHT for n in names}
            return {n: round(w / max_w, 4) for n, w in raw_weights.items()}

    def get_performance_report(self) -> dict:
        """Returns full performance breakdown by strategy and regime."""
        with self._lock:
            report = {}
            for strategy, regimes in self._data.items():
                report[strategy] = {}
                for regime, data in regimes.items():
                    wins = data["wins"]
                    losses = data["losses"]
                    total = wins + losses
                    report[strategy][regime] = {
                        "sample_count": data["sample_count"],
                        "win_rate": round(wins / total * 100, 1) if total > 0 else 0.0,
                        "total_pnl": round(data["total_pnl"], 2),
                        "avg_confidence": round(data["avg_confidence"], 3),
                    }
            return report

    def best_strategy_for_regime(
        self, regime: str, candidates: Optional[List[str]] = None
    ) -> Optional[str]:
        """Returns the strategy with highest weight for the current regime."""
        weights = self.get_strategy_weights(regime, candidates)
        if not weights:
            return None
        return max(weights, key=weights.get)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _compute_weight(self, regime_data: dict) -> float:
        """
        Computes a single float weight from a regime's performance data.
        Returns default weight if sample count is too low.
        """
        if not regime_data:
            return _DEFAULT_WEIGHT
        count = regime_data.get("sample_count", 0)
        if count < adaptive_config.min_samples_before_adjustment:
            return _DEFAULT_WEIGHT  # not enough data yet

        wins = regime_data.get("wins", 0)
        losses = regime_data.get("losses", 0)
        total = wins + losses
        if total == 0:
            return _DEFAULT_WEIGHT

        win_rate = wins / total
        total_pnl = regime_data.get("total_pnl", 0.0)
        avg_conf = regime_data.get("avg_confidence", 0.5)

        # Composite weight: win_rate × 0.5 + pnl_sign × 0.3 + confidence × 0.2
        pnl_component = min(1.0, max(0.0, (total_pnl + 5000) / 10000))  # normalized
        weight = win_rate * 0.5 + pnl_component * 0.3 + avg_conf * 0.2

        return max(_MIN_WEIGHT, min(_MAX_WEIGHT, weight * 2.0))  # scale to 0.1-3.0

    def _save(self) -> None:
        """Persist state to JSON file."""
        try:
            self._persistence_path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                data_copy = dict(self._data)
            self._persistence_path.write_text(
                json.dumps(data_copy, indent=2, default=str)
            )
        except Exception as e:
            logger.warning(f"Adaptive engine save failed: {e}")

    def _load(self) -> None:
        """Load persisted state from JSON file."""
        if not self._persistence_path.exists():
            return
        try:
            raw = json.loads(self._persistence_path.read_text())
            with self._lock:
                self._data = raw
            logger.info(f"Adaptive engine loaded from {self._persistence_path}")
        except Exception as e:
            logger.warning(f"Adaptive engine load failed: {e}")


# Module-level singleton
adaptive_engine = AdaptiveEngine()
