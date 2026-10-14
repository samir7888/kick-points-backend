"""Vote strategy — wraps legacy outcome selection logic."""

from __future__ import annotations

import random
from typing import Any

from app.core.config import get_settings
from app.integrations.kick.models import PredictionEvent

settings = get_settings()


class VoteStrategy:
    """Encapsulates the outcome selection algorithm from legacy/src/monitor.py.

    The algorithm (unchanged from legacy):
    - 70% pure random choice
    - 30% pick from the top-50% outcomes by return_rate (odds-weighted)
    Returns outcome_id or None (= don't vote).
    """

    @staticmethod
    def choose_outcome(prediction: PredictionEvent, user_settings: Any) -> str | None:
        """Return the outcome_id to vote on, or None to skip."""
        outcomes = prediction.outcomes
        if not outcomes:
            return None

        # Mirror the legacy "smart" strategy from monitor.py
        outcomes_with_odds = [
            {
                "outcome": o,
                "multiplier": o.return_rate,
                "id": o.id,
            }
            for o in outcomes
        ]

        if random.random() < 0.3 and outcomes_with_odds:
            sorted_outcomes = sorted(
                outcomes_with_odds,
                key=lambda x: float(x["multiplier"]),
                reverse=True,  # type: ignore[arg-type]
            )
            top_half = sorted_outcomes[: max(1, len(sorted_outcomes) // 2)]
            selected = random.choice(top_half)
        else:
            selected = random.choice(outcomes_with_odds)

        return str(selected["id"])
