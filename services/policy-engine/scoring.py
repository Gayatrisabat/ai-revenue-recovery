"""Expected-value scoring (docs/revised-architecture.md §8.3).

    expected_net_recovery
    = calibrated_probability_of_incremental_success × recoverable_amount
      - action_cost
      - operational_cost
      - risk_penalty

Implemented as plain integer/float arithmetic on minor currency units,
rounded down (banker's-rounding-free) so the engine never reports more
expected recovery than the math actually supports.
"""

from __future__ import annotations

import math
from typing import Literal

from domain.enums import DeclineCode, RiskTier
from domain.models import ActionCandidate, Economics

from .config import ESCALATE, EconomicTables, STOP_PURSUIT

EngineOutcome = Literal["ACTIONABLE_CANDIDATES", "NO_ACTION_ABOVE_THRESHOLD", "BLOCKED_BY_POLICY"]


def success_probability_for(
    candidate_key: str, root_cause: DeclineCode, tables: EconomicTables
) -> float:
    if candidate_key == STOP_PURSUIT:
        return 0.0
    if candidate_key == ESCALATE:
        return tables.escalation_success_probability
    return tables.success_probability(root_cause, candidate_key)


def compute_economics(
    candidate_key: str,
    *,
    recoverable_amount_minor: int,
    root_cause: DeclineCode,
    risk_tier: RiskTier,
    tables: EconomicTables,
) -> Economics:
    probability = success_probability_for(candidate_key, root_cause, tables)
    cost_entry = tables.cost(candidate_key)
    risk_penalty = tables.risk_penalty(risk_tier) if candidate_key != STOP_PURSUIT else 0

    expected_gross_recovery_minor = math.floor(probability * recoverable_amount_minor)
    expected_net_recovery_minor = (
        expected_gross_recovery_minor
        - cost_entry.action_cost_minor
        - cost_entry.operational_cost_minor
        - risk_penalty
    )

    return Economics(
        success_probability=probability,
        expected_gross_recovery_minor=expected_gross_recovery_minor,
        estimated_action_cost_minor=cost_entry.action_cost_minor + cost_entry.operational_cost_minor,
        risk_penalty_minor=risk_penalty,
        expected_net_recovery_minor=expected_net_recovery_minor,
    )


def clears_economic_threshold(candidate: ActionCandidate, minimum_expected_net_recovery_minor: int) -> bool:
    """AT-10: whether one candidate, on its own, justifies acting.

    stop_pursuit is excluded from ever "clearing" the bar: it is the
    null action (zero cost, zero recovery) and must never itself count
    as a reason to proceed -- it is the fallback when nothing else
    clears the bar, not a candidate competing to clear it.
    """
    return (
        candidate.candidate_id != STOP_PURSUIT
        and candidate.economics.expected_net_recovery_minor >= minimum_expected_net_recovery_minor
    )
