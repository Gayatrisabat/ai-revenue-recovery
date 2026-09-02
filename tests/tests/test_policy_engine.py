from __future__ import annotations

import pytest

from domain.enums import CaseState, DeclineCode
from domain.errors import InvalidStateTransition
from policy.config import ESCALATE, RETRY_24H, SEND_EMAIL, STOP_PURSUIT


# ---------------------------------------------------------------------------
# AT-04 / AT-05 / legal hold / subscription-status stops
# ---------------------------------------------------------------------------


class TestEligibilityStops:
    def test_opt_out_stop_reaches_terminal_state(self, case_factory, policy_engine, now, db):
        case = case_factory(CaseState.NORMALIZED, opted_out=True)
        result = policy_engine.run_eligibility(case, now=now)
        assert result.status == "BLOCKED"
        assert result.reason_code == "CUSTOMER_OPTED_OUT"
        assert result.case.state == CaseState.CUSTOMER_OPTED_OUT

    def test_opt_out_appends_eligibility_blocked_audit(self, case_factory, policy_engine, now, db):
        case = case_factory(CaseState.NORMALIZED, opted_out=True)
        result = policy_engine.run_eligibility(case, now=now)
        audit = db.audit_log.for_case(case.case_id)
        assert any(
            e.event_type == "ELIGIBILITY_BLOCKED" and e.reason_codes == ("CUSTOMER_OPTED_OUT",)
            for e in audit
        )

    def test_opt_out_case_cannot_transition_further(self, case_factory, policy_engine, now):
        case = case_factory(CaseState.NORMALIZED, opted_out=True)
        result = policy_engine.run_eligibility(case, now=now)
        from domain.enums import ActorType
        from domain.state_machine import transition

        with pytest.raises(InvalidStateTransition):
            transition(
                result.case,
                CaseState.CANDIDATES_GENERATED,
                actor_type=ActorType.POLICY_ENGINE,
                actor_version="test",
                now=now,
            )

    def test_dispute_stop_reaches_terminal_state(self, case_factory, policy_engine, now):
        case = case_factory(CaseState.NORMALIZED, disputed=True)
        result = policy_engine.run_eligibility(case, now=now)
        assert result.status == "BLOCKED"
        assert result.reason_code == "CUSTOMER_DISPUTED"
        assert result.case.state == CaseState.CUSTOMER_DISPUTED

    def test_legal_hold_stop_reaches_stopped_by_policy(self, case_factory, policy_engine, now):
        case = case_factory(CaseState.NORMALIZED, legal_hold=True)
        result = policy_engine.run_eligibility(case, now=now)
        assert result.status == "BLOCKED"
        assert result.reason_code == "LEGAL_OR_ACCOUNT_HOLD"
        assert result.case.state == CaseState.STOPPED_BY_POLICY

    def test_canceled_subscription_stop(self, case_factory, policy_engine, now):
        case = case_factory(CaseState.NORMALIZED, subscription_canceled=True)
        result = policy_engine.run_eligibility(case, now=now)
        assert result.status == "BLOCKED"
        assert result.reason_code == "SUBSCRIPTION_CANCELED"
        assert result.case.state == CaseState.STOPPED_BY_POLICY

    def test_already_paid_subscription_stop(self, case_factory, policy_engine, now):
        case = case_factory(CaseState.NORMALIZED, already_paid=True)
        result = policy_engine.run_eligibility(case, now=now)
        assert result.status == "BLOCKED"
        assert result.reason_code == "SUBSCRIPTION_ALREADY_PAID"
        assert result.case.state == CaseState.STOPPED_BY_POLICY

    def test_clean_case_passes_to_eligibility_checked(self, case_factory, policy_engine, now):
        case = case_factory(CaseState.NORMALIZED)
        result = policy_engine.run_eligibility(case, now=now)
        assert result.status == "PASSED"
        assert result.case.state == CaseState.ELIGIBILITY_CHECKED

    def test_passing_eligibility_appends_eligibility_checked_audit(
        self, case_factory, policy_engine, now, db
    ):
        case = case_factory(CaseState.NORMALIZED)
        policy_engine.run_eligibility(case, now=now)
        event_types = [e.event_type for e in db.audit_log.for_case(case.case_id)]
        assert "ELIGIBILITY_CHECKED" in event_types


# ---------------------------------------------------------------------------
# AT-09 candidate generation
# ---------------------------------------------------------------------------


class TestCandidateGenerationIntegration:
    def test_case_transitions_to_candidates_generated_then_action_scored_or_decision_pending(
        self, case_factory, policy_engine, now
    ):
        case = case_factory(CaseState.DIAGNOSED)
        result = policy_engine.generate_and_score(case, root_cause=DeclineCode.INSUFFICIENT_FUNDS, now=now)
        assert result.outcome == "ACTIONABLE_CANDIDATES"
        assert result.case.state == CaseState.DECISION_PENDING

    def test_candidates_generated_audit_lists_all_candidate_ids(
        self, case_factory, policy_engine, now, db
    ):
        case = case_factory(CaseState.DIAGNOSED)
        policy_engine.generate_and_score(case, root_cause=DeclineCode.INSUFFICIENT_FUNDS, now=now)
        audit = db.audit_log.for_case(case.case_id)
        gen_event = next(e for e in audit if e.event_type == "CANDIDATES_GENERATED")
        assert set(gen_event.payload["candidate_ids"]) == {
            "retry_after_24_hours",
            "retry_after_72_hours",
            "send_approved_email_template_01",
            "offer_approved_alternate_method",
            "escalate_to_human",
            "stop_pursuit",
        }

    def test_returned_candidates_come_from_deterministic_code_not_llm(
        self, case_factory, policy_engine, now
    ):
        case = case_factory(CaseState.DIAGNOSED)
        result = policy_engine.generate_and_score(case, root_cause=DeclineCode.INSUFFICIENT_FUNDS, now=now)
        for candidate in result.candidates:
            assert candidate.parameters["amount_minor"] == case.principal_amount_minor


# ---------------------------------------------------------------------------
# AT-07 variant: contact cap reached but non-contact candidates remain
# ---------------------------------------------------------------------------


class TestContactCapDoesNotStopWholeCaseWhenRetryStillEligible:
    def test_contact_capped_case_still_reaches_decision_pending_via_retry(
        self, case_factory, policy_engine, now
    ):
        case = case_factory(
            CaseState.DIAGNOSED, contact_count_week=2, principal_amount_minor=149900
        )
        result = policy_engine.generate_and_score(case, root_cause=DeclineCode.NETWORK_ERROR, now=now)
        contact_candidates = [
            c for c in result.candidates if c.candidate_id in ("send_approved_email_template_01", "offer_approved_alternate_method")
        ]
        assert all(c.eligibility.allowed is False for c in contact_candidates)
        assert all(c.eligibility.reason_codes == ("CONTACT_CAP_REACHED",) for c in contact_candidates)
        # Retry is still eligible for network_error, so the case proceeds.
        assert result.outcome == "ACTIONABLE_CANDIDATES"
        assert result.case.state == CaseState.DECISION_PENDING


# ---------------------------------------------------------------------------
# AT-07 #2: no eligible non-contact candidates -> STOPPED_BY_POLICY
#
# Not naturally reachable through the public API, because
# escalate_to_human is always eligible by design. Exercised here via a
# monkeypatch that forces every candidate but stop_pursuit ineligible,
# to prove the engine's own stop-if-nothing-actionable branch works.
# ---------------------------------------------------------------------------


class TestNoActionableCandidatesStopsCase:
    def test_all_non_contact_candidates_ineligible_stops_the_case(
        self, case_factory, policy_engine, now, db, monkeypatch
    ):
        import policy.candidates as candidates_module
        from domain.models import EligibilityResult

        def _force_ineligible(candidate_key, case, config, root_cause, now):
            if candidate_key == STOP_PURSUIT:
                return EligibilityResult(allowed=True, reason_codes=("BASELINE_STOP_AVAILABLE",))
            return EligibilityResult(allowed=False, reason_codes=("FORCED_INELIGIBLE_FOR_TEST",))

        monkeypatch.setattr(candidates_module, "check_candidate_eligibility", _force_ineligible)

        case = case_factory(CaseState.DIAGNOSED)
        result = policy_engine.generate_and_score(case, root_cause=DeclineCode.INSUFFICIENT_FUNDS, now=now)

        assert result.outcome == "BLOCKED_BY_POLICY"
        assert result.case.state == CaseState.STOPPED_BY_POLICY

        audit = db.audit_log.for_case(case.case_id)
        stop_event = next(e for e in audit if e.event_type == "CASE_STOPPED")
        assert stop_event.reason_codes == ("NO_ACTIONABLE_CANDIDATES",)


# ---------------------------------------------------------------------------
# AT-10 economic threshold enforcement + variant
# ---------------------------------------------------------------------------


class TestEconomicThresholdEnforcement:
    def test_all_candidates_below_threshold_stops_case(self, case_factory, policy_engine, now, db):
        # A tiny recoverable amount makes every candidate's expected net
        # recovery negative or near-zero, well below the 500-minor floor.
        case = case_factory(CaseState.DIAGNOSED, principal_amount_minor=100)
        result = policy_engine.generate_and_score(case, root_cause=DeclineCode.INSUFFICIENT_FUNDS, now=now)

        assert result.outcome == "NO_ACTION_ABOVE_THRESHOLD"
        assert result.case.state == CaseState.STOPPED_BY_POLICY

        audit = db.audit_log.for_case(case.case_id)
        assert any(e.event_type == "CANDIDATES_SCORED" for e in audit)
        stop_event = next(e for e in audit if e.event_type == "CASE_STOPPED")
        assert stop_event.reason_codes == ("BELOW_ECONOMIC_THRESHOLD",)

    def test_at_least_one_above_threshold_proceeds_to_decision_pending(
        self, case_factory, policy_engine, now
    ):
        case = case_factory(CaseState.DIAGNOSED, principal_amount_minor=149900)
        result = policy_engine.generate_and_score(case, root_cause=DeclineCode.INSUFFICIENT_FUNDS, now=now)
        assert result.outcome == "ACTIONABLE_CANDIDATES"
        assert result.case.state == CaseState.DECISION_PENDING


# ---------------------------------------------------------------------------
# Human approval threshold surfaced by the engine
# ---------------------------------------------------------------------------


class TestHumanApprovalFlagSurfacedByEngine:
    def test_high_value_case_flagged_for_human_approval(self, case_factory, policy_engine, now):
        case = case_factory(
            CaseState.DIAGNOSED,
            principal_amount_minor=policy_engine.config.high_value_approval_threshold_minor,
        )
        result = policy_engine.generate_and_score(case, root_cause=DeclineCode.INSUFFICIENT_FUNDS, now=now)
        assert result.requires_human_approval is True

    def test_low_value_case_not_flagged(self, case_factory, policy_engine, now):
        case = case_factory(CaseState.DIAGNOSED, principal_amount_minor=5000)
        result = policy_engine.generate_and_score(case, root_cause=DeclineCode.INSUFFICIENT_FUNDS, now=now)
        assert result.requires_human_approval is False


# ---------------------------------------------------------------------------
# Unknown-decline fallback, end to end
# ---------------------------------------------------------------------------


class TestUnknownDeclineFallbackIntegration:
    def test_unknown_decline_leaves_only_escalate_and_stop_eligible(
        self, case_factory, policy_engine, now
    ):
        case = case_factory(CaseState.DIAGNOSED, principal_amount_minor=149900)
        result = policy_engine.generate_and_score(case, root_cause=DeclineCode.UNKNOWN, now=now)
        eligible_ids = {c.candidate_id for c in result.candidates if c.eligibility.allowed}
        assert eligible_ids == {ESCALATE, STOP_PURSUIT}
