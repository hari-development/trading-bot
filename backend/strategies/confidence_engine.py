"""
Weighted Confidence Engine — Phase 7: AI Decision Layer.

Combines multiple indicator signals into a single 0.0-1.0 confidence score.
Only trades where confidence >= min_confidence_threshold are executed.

Weights are configured in config.settings.ConfidenceConfig and can be
adjusted without touching strategy code.
"""
from typing import List, Tuple

from config.settings import confidence_config
from utils.logger import get_logger

logger = get_logger("confidence_engine")


class WeightedConfidenceEngine:
    """
    Computes a weighted confidence score from a list of indicator checks.

    Usage:
        engine = WeightedConfidenceEngine()
        score = engine.compute([
            ("ema_trend", True, 1.0, confidence_config.ema_trend_weight),
            ("supertrend", True, 1.0, confidence_config.supertrend_weight),
            ("volume", True, 0.8, confidence_config.volume_weight),
            ...
        ])
        # returns 0.0-1.0 float

    Each check tuple: (name: str, passes: bool, partial_score: float, weight: float)
    - name: human-readable indicator name for logging
    - passes: True if check succeeded
    - partial_score: 0.0-1.0 quality of the pass (e.g. 0.7 for moderate, 1.0 for strong)
    - weight: fractional importance of this indicator (should sum to 1.0 across all)
    """

    def compute(
        self,
        checks: List[Tuple[str, bool, float, float]],
    ) -> float:
        """
        Returns a 0.0-1.0 confidence score.

        Args:
            checks: list of (name, passes, partial_score, weight) tuples

        Returns:
            float in [0.0, 1.0]
        """
        if not checks:
            return 0.0

        total_weight = sum(w for _, _, _, w in checks)
        if total_weight == 0:
            return 0.0

        weighted_sum = 0.0
        for name, passes, partial_score, weight in checks:
            contribution = (partial_score if passes else 0.0) * weight
            weighted_sum += contribution

        score = weighted_sum / total_weight
        return round(min(1.0, max(0.0, score)), 4)

    def passes_threshold(self, score: float) -> bool:
        """Returns True if score meets the minimum confidence threshold."""
        return score >= confidence_config.min_confidence_threshold

    def get_size_multiplier(self, score: float) -> float:
        """
        Returns a position size multiplier (0.5-1.0) based on confidence.
        Low confidence = smaller position; high confidence = full position.
        """
        if score < confidence_config.min_confidence_threshold:
            return 0.0  # don't trade
        if score >= confidence_config.high_confidence_threshold:
            return 1.0  # full size
        # Linear interpolation between thresholds
        lo = confidence_config.min_confidence_threshold
        hi = confidence_config.high_confidence_threshold
        return 0.5 + 0.5 * (score - lo) / (hi - lo)


# Module-level singleton
confidence_engine = WeightedConfidenceEngine()
