from __future__ import annotations

import math

import pytest

from domain.enums import ActionType, DeclineCode, RiskTier
from domain.models import ActionCandidate, EligibilityResult
from policy.config import ESCALATE, RETRY_24H, SEND_EMAIL, STOP_PURSUIT
from policy.scoring import clears_economic_threshold, compute_economics, success_probability_for


class TestSuccessProbabilityFor:
    def test_stop_pursuit_has_zero_probability(self, economic_tables):
        assert success_probability_for(STOP_PURSUIT, DeclineCode.INSUFFICIENT_FUNDS, economic_tables) == 0.0

    def test_escalate_uses_flat_escalation_probability(self, economic_tables):
        prob = success_probability_for(ESCALATE, DeclineCode.NETWORK_ERROR, economic_tables)
        assert prob == economic_tables.escalation_success_probability

    def test_retry_uses_decline_code_table(self, economic_tables):
        prob = success_probability_for(RETRY_24H, DeclineCode.NETWORK_ERROR, economic_tables)
        assert prob == economic_tables.success_probability_by_decline_code[DeclineCode.NETWORK_ERROR][RETRY_24H]


class TestComputeEconomicsFormula:
    @pytest.mark.parametrize(
        "candidate_key,root_cause",
        [
            (RETRY_24H, DeclineCode.INSUFFICIENT_FUNDS),
            (SEND_EMAIL, DeclineCode.INSUFFICIENT_FUNDS),
            (ESCALATE, DeclineCode.EXPIRED_CARD),
        ],
    )
    def test_formula_matches_probability_times_amount_minus_costs(
        self, economic_tables, candidate_key, root_cause
    ):
        recoverable_amount_minor = 149900
        risk_tier = RiskTier.STANDARD

        economics = compute_economics(
            candidate_key,
            recoverable_amount_minor=recoverable_amount_minor,
            root_cause=root_cause,
            risk_tier=risk_tier,
            tables=economic_tables,
        )

        expected_probability = success_probability_for(candidate_key, root_cause, economic_tables)
        cost = economic_tables.cost(candidate_key)
        risk_penalty = economic_tables.risk_penalty(risk_tier)
        expected_gross = math.floor(expected_probability * recoverable_amount_minor)
        expected_net = (
            expected_gross - cost.action_cost_minor - cost.operational_cost_minor - risk_penalty
        )

        assert economics.success_probability == expected_probability
        assert economics.expected_gross_recovery_minor == expected_gross
        assert economics.expected_net_recovery_minor == expected_net

    def test_stop_pursuit_has_zero_cost_and_zero_recovery(self, economic_tables):
        economics = compute_economics(
            STOP_PURSUIT,
            recoverable_amount_minor=149900,
            root_cause=DeclineCode.UNKNOWN,
            risk_tier=RiskTier.HIGH,
            tables=economic_tables,
        )
        assert economics.expected_gross_recovery_minor == 0
        assert economics.risk_penalty_minor == 0
        assert economics.expected_net_recovery_minor == 0

    @pytest.mark.parametrize(
        "risk_tier,expected_penalty",
        [
            (RiskTier.STANDARD, 0),
            (RiskTier.ELEVATED, 1000),
            (RiskTier.HIGH, 5000),
        ],
    )
    def test_risk_penalty_scales_with_risk_tier(self, economic_tables, risk_tier, expected_penalty):
        economics = compute_economics(
            RETRY_24H,
            recoverable_amount_minor=100000,
            root_cause=DeclineCode.NETWORK_ERROR,
            risk_tier=risk_tier,
            tables=economic_tables,
        )
        assert economics.risk_penalty_minor == expected_penalty

    def test_higher_risk_tier_lowers_net_recovery(self, economic_tables):
        standard = compute_economics(
            RETRY_24H,
            recoverable_amount_minor=100000,
            root_cause=DeclineCode.NETWORK_ERROR,
            risk_tier=RiskTier.STANDARD,
            tables=economic_tables,
        )
        high = compute_economics(
            RETRY_24H,
            recoverable_amount_minor=100000,
            root_cause=DeclineCode.NETWORK_ERROR,
            risk_tier=RiskTier.HIGH,
            tables=economic_tables,
        )
        assert high.expected_net_recovery_minor < standard.expected_net_recovery_minor

    def test_zero_probability_root_cause_never_produces_positive_recovery(self, economic_tables):
        # stolen_card is 0.0 across the board.
        economics = compute_economics(
            RETRY_24H,
            recoverable_amount_minor=999999,
            root_cause=DeclineCode.STOLEN_CARD,
            risk_tier=RiskTier.STANDARD,
            tables=economic_tables,
        )
        assert economics.expected_gross_recovery_minor == 0
        assert economics.expected_net_recovery_minor < 0  # costs still apply


def _candidate(candidate_id: str, expected_net_recovery_minor: int) -> ActionCandidate:
    from domain.models import Economics

    return ActionCandidate(
        candidate_id=candidate_id,
        case_id="case_001",
        action_type=ActionType.STOP_PURSUIT,
        parameters={},
        eligibility=EligibilityResult(allowed=True),
        economics=Economics(
            success_probability=0.5,
            expected_gross_recovery_minor=max(expected_net_recovery_minor, 0) + 100,
            estimated_action_cost_minor=100,
            risk_penalty_minor=0,
            expected_net_recovery_minor=expected_net_recovery_minor,
        ),
    )


class TestClearsEconomicThreshold:
    def test_candidate_above_threshold_clears(self):
        candidate = _candidate(RETRY_24H, 600)
        assert clears_economic_threshold(candidate, minimum_expected_net_recovery_minor=500) is True

    def test_candidate_exactly_at_threshold_clears(self):
        candidate = _candidate(RETRY_24H, 500)
        assert clears_economic_threshold(candidate, minimum_expected_net_recovery_minor=500) is True

    def test_candidate_below_threshold_does_not_clear(self):
        candidate = _candidate(RETRY_24H, 499)
        assert clears_economic_threshold(candidate, minimum_expected_net_recovery_minor=500) is False

    def test_stop_pursuit_never_clears_even_with_high_value(self):
        candidate = _candidate(STOP_PURSUIT, 10_000_000)
        assert clears_economic_threshold(candidate, minimum_expected_net_recovery_minor=500) is False
