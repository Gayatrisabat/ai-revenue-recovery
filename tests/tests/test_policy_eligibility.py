from __future__ import annotations

from datetime import timedelta

import pytest

from domain.enums import CaseState, DeclineCode
from policy.config import ESCALATE, OFFER_ALT_METHOD, RETRY_24H, RETRY_72H, SEND_EMAIL, STOP_PURSUIT
from policy.eligibility import check_candidate_eligibility, check_case_level_stop


# ---------------------------------------------------------------------------
# Case-level absolute stops
# ---------------------------------------------------------------------------

CASE_LEVEL_STOP_TABLE = [
    # (case overrides, expected reason_code, expected target_state)
    ({"opted_out": True}, "CUSTOMER_OPTED_OUT", CaseState.CUSTOMER_OPTED_OUT),
    ({"disputed": True}, "CUSTOMER_DISPUTED", CaseState.CUSTOMER_DISPUTED),
    ({"legal_hold": True}, "LEGAL_OR_ACCOUNT_HOLD", CaseState.STOPPED_BY_POLICY),
    ({"subscription_canceled": True}, "SUBSCRIPTION_CANCELED", CaseState.STOPPED_BY_POLICY),
    ({"already_paid": True}, "SUBSCRIPTION_ALREADY_PAID", CaseState.STOPPED_BY_POLICY),
]


class TestCaseLevelStops:
    @pytest.mark.parametrize("overrides,reason_code,target_state", CASE_LEVEL_STOP_TABLE)
    def test_each_stop_condition_is_detected(
        self, case_factory, policy_config, overrides, reason_code, target_state
    ):
        case = case_factory(CaseState.NORMALIZED, **overrides)
        result = check_case_level_stop(case, policy_config)
        assert result is not None
        assert result.reason_code == reason_code
        assert result.target_state == target_state

    def test_clean_case_has_no_stop(self, case_factory, policy_config):
        case = case_factory(CaseState.NORMALIZED)
        assert check_case_level_stop(case, policy_config) is None

    def test_opt_out_takes_priority_over_dispute(self, case_factory, policy_config):
        case = case_factory(CaseState.NORMALIZED, opted_out=True, disputed=True)
        result = check_case_level_stop(case, policy_config)
        assert result.reason_code == "CUSTOMER_OPTED_OUT"

    def test_dispute_takes_priority_over_legal_hold(self, case_factory, policy_config):
        case = case_factory(CaseState.NORMALIZED, disputed=True, legal_hold=True)
        result = check_case_level_stop(case, policy_config)
        assert result.reason_code == "CUSTOMER_DISPUTED"

    def test_legal_hold_takes_priority_over_subscription_status(self, case_factory, policy_config):
        case = case_factory(CaseState.NORMALIZED, legal_hold=True, subscription_canceled=True)
        result = check_case_level_stop(case, policy_config)
        assert result.reason_code == "LEGAL_OR_ACCOUNT_HOLD"

    def test_canceled_takes_priority_over_already_paid(self, case_factory, policy_config):
        case = case_factory(CaseState.NORMALIZED, subscription_canceled=True, already_paid=True)
        result = check_case_level_stop(case, policy_config)
        assert result.reason_code == "SUBSCRIPTION_CANCELED"


# ---------------------------------------------------------------------------
# Candidate-level gating: contact cap (AT-07)
# ---------------------------------------------------------------------------

CONTACT_CANDIDATES = [SEND_EMAIL, OFFER_ALT_METHOD]


class TestContactCapEnforcement:
    @pytest.mark.parametrize("candidate_key", CONTACT_CANDIDATES)
    def test_contact_cap_reached_blocks_contact_candidates(
        self, case_factory, policy_config, now, candidate_key
    ):
        case = case_factory(
            CaseState.DIAGNOSED, contact_count_week=policy_config.max_customer_contacts_per_week
        )
        result = check_candidate_eligibility(
            candidate_key, case, policy_config, DeclineCode.INSUFFICIENT_FUNDS, now
        )
        assert result.allowed is False
        assert result.reason_codes == ("CONTACT_CAP_REACHED",)

    @pytest.mark.parametrize("candidate_key", CONTACT_CANDIDATES)
    def test_below_contact_cap_is_allowed(self, case_factory, policy_config, now, candidate_key):
        case = case_factory(
            CaseState.DIAGNOSED, contact_count_week=policy_config.max_customer_contacts_per_week - 1
        )
        result = check_candidate_eligibility(
            candidate_key, case, policy_config, DeclineCode.INSUFFICIENT_FUNDS, now
        )
        assert result.allowed is True

    @pytest.mark.parametrize("candidate_key", [RETRY_24H, RETRY_72H, ESCALATE, STOP_PURSUIT])
    def test_contact_cap_does_not_block_non_contact_candidates(
        self, case_factory, policy_config, now, candidate_key
    ):
        case = case_factory(
            CaseState.DIAGNOSED, contact_count_week=policy_config.max_customer_contacts_per_week
        )
        result = check_candidate_eligibility(
            candidate_key, case, policy_config, DeclineCode.INSUFFICIENT_FUNDS, now
        )
        assert result.allowed is True


# ---------------------------------------------------------------------------
# Candidate-level gating: cooldown window (AT-06)
# ---------------------------------------------------------------------------


class TestCooldownEnforcement:
    @pytest.mark.parametrize("candidate_key", CONTACT_CANDIDATES)
    def test_within_cooldown_window_blocks_contact_candidates(
        self, case_factory, policy_config, now, candidate_key
    ):
        last_contact = now - timedelta(hours=12)  # cooldown is 24h
        case = case_factory(CaseState.DIAGNOSED, last_contact_at=last_contact)
        result = check_candidate_eligibility(
            candidate_key, case, policy_config, DeclineCode.INSUFFICIENT_FUNDS, now
        )
        assert result.allowed is False
        assert result.reason_codes == ("COOLDOWN_ACTIVE",)

    @pytest.mark.parametrize("candidate_key", CONTACT_CANDIDATES)
    def test_cooldown_expired_allows_contact_candidates(
        self, case_factory, policy_config, now, candidate_key
    ):
        last_contact = now - timedelta(hours=25)  # cooldown is 24h
        case = case_factory(CaseState.DIAGNOSED, last_contact_at=last_contact)
        result = check_candidate_eligibility(
            candidate_key, case, policy_config, DeclineCode.INSUFFICIENT_FUNDS, now
        )
        assert result.allowed is True

    def test_never_contacted_has_no_cooldown(self, case_factory, policy_config, now):
        case = case_factory(CaseState.DIAGNOSED, last_contact_at=None)
        result = check_candidate_eligibility(
            SEND_EMAIL, case, policy_config, DeclineCode.INSUFFICIENT_FUNDS, now
        )
        assert result.allowed is True

    @pytest.mark.parametrize("candidate_key", [RETRY_24H, RETRY_72H, ESCALATE, STOP_PURSUIT])
    def test_cooldown_does_not_block_non_contact_candidates(
        self, case_factory, policy_config, now, candidate_key
    ):
        last_contact = now - timedelta(hours=1)
        case = case_factory(CaseState.DIAGNOSED, last_contact_at=last_contact)
        result = check_candidate_eligibility(
            candidate_key, case, policy_config, DeclineCode.INSUFFICIENT_FUNDS, now
        )
        assert result.allowed is True


# ---------------------------------------------------------------------------
# Candidate-level gating: retry cap (AT-08)
# ---------------------------------------------------------------------------


class TestRetryCapEnforcement:
    @pytest.mark.parametrize("candidate_key", [RETRY_24H, RETRY_72H])
    def test_retry_cap_reached_blocks_retry_candidates(
        self, case_factory, policy_config, now, candidate_key
    ):
        case = case_factory(
            CaseState.DIAGNOSED, retry_count_episode=policy_config.max_payment_retries_per_episode
        )
        result = check_candidate_eligibility(
            candidate_key, case, policy_config, DeclineCode.NETWORK_ERROR, now
        )
        assert result.allowed is False
        assert result.reason_codes == ("RETRY_CAP_REACHED",)

    @pytest.mark.parametrize("candidate_key", [RETRY_24H, RETRY_72H])
    def test_below_retry_cap_is_allowed(self, case_factory, policy_config, now, candidate_key):
        case = case_factory(
            CaseState.DIAGNOSED, retry_count_episode=policy_config.max_payment_retries_per_episode - 1
        )
        result = check_candidate_eligibility(
            candidate_key, case, policy_config, DeclineCode.NETWORK_ERROR, now
        )
        assert result.allowed is True

    @pytest.mark.parametrize("candidate_key", CONTACT_CANDIDATES + [ESCALATE, STOP_PURSUIT])
    def test_retry_cap_does_not_block_messaging_or_escalation(
        self, case_factory, policy_config, now, candidate_key
    ):
        case = case_factory(
            CaseState.DIAGNOSED, retry_count_episode=policy_config.max_payment_retries_per_episode
        )
        result = check_candidate_eligibility(
            candidate_key, case, policy_config, DeclineCode.NETWORK_ERROR, now
        )
        assert result.allowed is True


# ---------------------------------------------------------------------------
# Root-cause based action eligibility
# ---------------------------------------------------------------------------


class TestActionEligibilityByRootCause:
    @pytest.mark.parametrize("candidate_key", [RETRY_24H, RETRY_72H])
    @pytest.mark.parametrize("root_cause", [DeclineCode.EXPIRED_CARD, DeclineCode.STOLEN_CARD])
    def test_retry_ineligible_for_expired_or_stolen_card(
        self, case_factory, policy_config, now, candidate_key, root_cause
    ):
        case = case_factory(CaseState.DIAGNOSED)
        result = check_candidate_eligibility(candidate_key, case, policy_config, root_cause, now)
        assert result.allowed is False
        assert result.reason_codes == ("NOT_APPLICABLE_FOR_ROOT_CAUSE",)

    @pytest.mark.parametrize("candidate_key", CONTACT_CANDIDATES)
    def test_contact_ineligible_for_stolen_card(self, case_factory, policy_config, now, candidate_key):
        case = case_factory(CaseState.DIAGNOSED)
        result = check_candidate_eligibility(
            candidate_key, case, policy_config, DeclineCode.STOLEN_CARD, now
        )
        assert result.allowed is False
        assert result.reason_codes == ("NOT_APPLICABLE_FOR_ROOT_CAUSE",)

    @pytest.mark.parametrize("candidate_key", CONTACT_CANDIDATES)
    def test_contact_still_eligible_for_expired_card(self, case_factory, policy_config, now, candidate_key):
        # Only the retry action is futile for an expired card -- offering
        # an alternate method or emailing the customer is still sensible.
        case = case_factory(CaseState.DIAGNOSED)
        result = check_candidate_eligibility(
            candidate_key, case, policy_config, DeclineCode.EXPIRED_CARD, now
        )
        assert result.allowed is True

    @pytest.mark.parametrize("candidate_key", [ESCALATE, STOP_PURSUIT])
    @pytest.mark.parametrize("root_cause", [DeclineCode.STOLEN_CARD, DeclineCode.EXPIRED_CARD])
    def test_escalate_and_stop_always_eligible_regardless_of_root_cause(
        self, case_factory, policy_config, now, candidate_key, root_cause
    ):
        case = case_factory(CaseState.DIAGNOSED)
        result = check_candidate_eligibility(candidate_key, case, policy_config, root_cause, now)
        assert result.allowed is True


# ---------------------------------------------------------------------------
# Unknown-decline fallback
# ---------------------------------------------------------------------------


class TestUnknownDeclineFallback:
    @pytest.mark.parametrize("candidate_key", [RETRY_24H, RETRY_72H] + CONTACT_CANDIDATES)
    def test_unknown_decline_blocks_retry_and_contact_candidates(
        self, case_factory, policy_config, now, candidate_key
    ):
        case = case_factory(CaseState.DIAGNOSED)
        result = check_candidate_eligibility(candidate_key, case, policy_config, DeclineCode.UNKNOWN, now)
        assert result.allowed is False
        assert result.reason_codes == ("UNKNOWN_DECLINE_FALLBACK",)

    @pytest.mark.parametrize("candidate_key", [ESCALATE, STOP_PURSUIT])
    def test_unknown_decline_still_allows_escalation_and_stop(
        self, case_factory, policy_config, now, candidate_key
    ):
        case = case_factory(CaseState.DIAGNOSED)
        result = check_candidate_eligibility(candidate_key, case, policy_config, DeclineCode.UNKNOWN, now)
        assert result.allowed is True
