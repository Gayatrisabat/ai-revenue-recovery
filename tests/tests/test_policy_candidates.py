from __future__ import annotations

import pytest

from domain.enums import ActionType, CaseState, DeclineCode
from policy.candidates import (
    CANDIDATE_ACTION_TYPES,
    EMAIL_TEMPLATE_ID,
    generate_candidates,
    requires_human_approval,
    validate_amount_integrity,
)
from policy.config import (
    ALL_CANDIDATE_KEYS,
    ESCALATE,
    OFFER_ALT_METHOD,
    RETRY_24H,
    RETRY_72H,
    SEND_EMAIL,
    STOP_PURSUIT,
)
from policy.errors import AmountIntegrityError


class TestFixedCandidateSet:
    def test_generates_exactly_the_six_required_candidates(
        self, case_factory, policy_config, economic_tables, now
    ):
        case = case_factory(CaseState.DIAGNOSED)
        candidates = generate_candidates(
            case, root_cause=DeclineCode.INSUFFICIENT_FUNDS, config=policy_config, tables=economic_tables, now=now
        )
        candidate_ids = {c.candidate_id for c in candidates}
        assert candidate_ids == set(ALL_CANDIDATE_KEYS)
        assert candidate_ids == {
            RETRY_24H,
            RETRY_72H,
            SEND_EMAIL,
            OFFER_ALT_METHOD,
            ESCALATE,
            STOP_PURSUIT,
        }

    def test_ineligible_candidates_are_still_returned(
        self, case_factory, policy_config, economic_tables, now
    ):
        # Opted-out-style blocking isn't checked here (that's a case-level
        # stop before generation even runs) -- retry cap is a good example
        # of a candidate-level block that still produces a candidate row.
        case = case_factory(
            CaseState.DIAGNOSED, retry_count_episode=policy_config.max_payment_retries_per_episode
        )
        candidates = generate_candidates(
            case, root_cause=DeclineCode.NETWORK_ERROR, config=policy_config, tables=economic_tables, now=now
        )
        retry_candidates = [c for c in candidates if c.candidate_id in (RETRY_24H, RETRY_72H)]
        assert len(retry_candidates) == 2
        assert all(c.eligibility.allowed is False for c in retry_candidates)

    @pytest.mark.parametrize(
        "candidate_key,expected_action_type",
        [
            (RETRY_24H, ActionType.RETRY_PAYMENT),
            (RETRY_72H, ActionType.RETRY_PAYMENT),
            (SEND_EMAIL, ActionType.SEND_APPROVED_EMAIL_TEMPLATE),
            (OFFER_ALT_METHOD, ActionType.OFFER_APPROVED_ALTERNATE_METHOD),
            (ESCALATE, ActionType.ESCALATE_TO_HUMAN),
            (STOP_PURSUIT, ActionType.STOP_PURSUIT),
        ],
    )
    def test_action_type_mapping(self, candidate_key, expected_action_type):
        assert CANDIDATE_ACTION_TYPES[candidate_key] == expected_action_type


class TestCandidateParameters:
    def test_retry_24h_has_24_hour_delay(self, case_factory, policy_config, economic_tables, now):
        case = case_factory(CaseState.DIAGNOSED)
        candidates = generate_candidates(
            case, root_cause=DeclineCode.NETWORK_ERROR, config=policy_config, tables=economic_tables, now=now
        )
        candidate = next(c for c in candidates if c.candidate_id == RETRY_24H)
        assert candidate.parameters["delay_hours"] == 24

    def test_retry_72h_has_72_hour_delay(self, case_factory, policy_config, economic_tables, now):
        case = case_factory(CaseState.DIAGNOSED)
        candidates = generate_candidates(
            case, root_cause=DeclineCode.NETWORK_ERROR, config=policy_config, tables=economic_tables, now=now
        )
        candidate = next(c for c in candidates if c.candidate_id == RETRY_72H)
        assert candidate.parameters["delay_hours"] == 72

    def test_email_candidate_uses_approved_template_id(
        self, case_factory, policy_config, economic_tables, now
    ):
        case = case_factory(CaseState.DIAGNOSED)
        candidates = generate_candidates(
            case, root_cause=DeclineCode.INSUFFICIENT_FUNDS, config=policy_config, tables=economic_tables, now=now
        )
        candidate = next(c for c in candidates if c.candidate_id == SEND_EMAIL)
        assert candidate.parameters["template_id"] == EMAIL_TEMPLATE_ID

    def test_every_candidate_amount_equals_case_principal(
        self, case_factory, policy_config, economic_tables, now
    ):
        case = case_factory(CaseState.DIAGNOSED, principal_amount_minor=234500)
        candidates = generate_candidates(
            case, root_cause=DeclineCode.INSUFFICIENT_FUNDS, config=policy_config, tables=economic_tables, now=now
        )
        for candidate in candidates:
            assert candidate.parameters["amount_minor"] == 234500

    def test_candidate_parameters_are_immutable_after_generation(
        self, case_factory, policy_config, economic_tables, now
    ):
        # ActionCandidate is a frozen dataclass -- reassigning any field
        # must raise, proving the LLM (or anything downstream) cannot
        # mutate a generated candidate's parameters.
        case = case_factory(CaseState.DIAGNOSED)
        candidate = generate_candidates(
            case, root_cause=DeclineCode.NETWORK_ERROR, config=policy_config, tables=economic_tables, now=now
        )[0]
        with pytest.raises(Exception):
            candidate.parameters = {"delay_hours": 999}  # type: ignore[misc]


class TestAmountIntegrity:
    def test_matching_amount_passes(self, case_factory):
        case = case_factory(CaseState.DIAGNOSED, principal_amount_minor=100000)
        validate_amount_integrity(100000, case)  # must not raise

    def test_mismatched_amount_raises(self, case_factory):
        case = case_factory(CaseState.DIAGNOSED, principal_amount_minor=100000)
        with pytest.raises(AmountIntegrityError):
            validate_amount_integrity(99999, case)

    def test_generation_always_satisfies_amount_integrity(
        self, case_factory, policy_config, economic_tables, now
    ):
        case = case_factory(CaseState.DIAGNOSED, principal_amount_minor=77700)
        # Should not raise -- the generator only ever uses the case's own
        # principal amount.
        candidates = generate_candidates(
            case, root_cause=DeclineCode.INSUFFICIENT_FUNDS, config=policy_config, tables=economic_tables, now=now
        )
        assert all(c.parameters["amount_minor"] == 77700 for c in candidates)


class TestHumanApprovalThreshold:
    def test_below_threshold_does_not_require_approval(self, case_factory, policy_config):
        case = case_factory(
            CaseState.DIAGNOSED,
            principal_amount_minor=policy_config.high_value_approval_threshold_minor - 1,
        )
        assert requires_human_approval(case, policy_config) is False

    def test_at_threshold_requires_approval(self, case_factory, policy_config):
        case = case_factory(
            CaseState.DIAGNOSED, principal_amount_minor=policy_config.high_value_approval_threshold_minor
        )
        assert requires_human_approval(case, policy_config) is True

    def test_above_threshold_requires_approval(self, case_factory, policy_config):
        case = case_factory(
            CaseState.DIAGNOSED,
            principal_amount_minor=policy_config.high_value_approval_threshold_minor + 1,
        )
        assert requires_human_approval(case, policy_config) is True
