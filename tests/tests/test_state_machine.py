from __future__ import annotations

import itertools

import pytest

from domain.enums import ActorType, CaseState
from domain.errors import InvalidStateTransition
from domain.state_machine import (
    ALLOWED_TRANSITIONS,
    assert_valid_transition,
    is_transition_allowed,
    transition,
)

CS = CaseState

# ---------------------------------------------------------------------------
# Exhaustive coverage: every ordered pair of states is tested, either as a
# required-valid transition or a required-forbidden transition. This means
# adding a state without updating ALLOWED_TRANSITIONS breaks the test suite.
# ---------------------------------------------------------------------------
ALL_STATES = list(CaseState)
ALL_PAIRS = list(itertools.product(ALL_STATES, ALL_STATES))

VALID_PAIRS = [
    (frm, to) for frm in ALL_STATES for to in ALLOWED_TRANSITIONS[frm]
]
FORBIDDEN_PAIRS = [pair for pair in ALL_PAIRS if pair not in VALID_PAIRS]


class TestExhaustiveTransitionMatrix:
    """One assertion per (from_state, to_state) pair in the entire state
    space -- every valid transition and every forbidden transition."""

    @pytest.mark.parametrize("from_state,to_state", VALID_PAIRS)
    def test_valid_transition_is_allowed(self, from_state, to_state):
        assert is_transition_allowed(from_state, to_state) is True
        # Must not raise.
        assert_valid_transition(from_state, to_state)

    @pytest.mark.parametrize("from_state,to_state", FORBIDDEN_PAIRS)
    def test_forbidden_transition_is_rejected(self, from_state, to_state):
        assert is_transition_allowed(from_state, to_state) is False
        with pytest.raises(InvalidStateTransition):
            assert_valid_transition(from_state, to_state)


class TestTransitionMatrixSanity:
    def test_every_state_has_an_adjacency_entry(self):
        assert set(ALLOWED_TRANSITIONS.keys()) == set(CaseState)

    def test_no_self_loops(self):
        for frm, to_set in ALLOWED_TRANSITIONS.items():
            assert frm not in to_set, f"{frm} must not transition to itself"

    def test_terminal_states_have_limited_or_no_outgoing_edges(self):
        # Fully terminal per docs/revised-architecture.md §5: no automated
        # transition ever leaves these.
        for state in (
            CS.RECOVERED,
            CS.ESCALATED_TO_HUMAN,
            CS.CUSTOMER_OPTED_OUT,
            CS.CUSTOMER_DISPUTED,
            CS.EXPIRED,
        ):
            assert ALLOWED_TRANSITIONS[state] == frozenset()

    def test_expected_number_of_states(self):
        # 11 active + 7 terminal = 18 (see enums.CaseState)
        assert len(CaseState) == 18


# ---------------------------------------------------------------------------
# Named tests for the specific scenarios called out in
# docs/revised-architecture.md, docs/demo-script.md and
# docs/acceptance-tests.md, exercised through the real `transition()`
# function (not just the predicate) so the RecoveryCase/AuditEvent side
# effects are verified too.
# ---------------------------------------------------------------------------


class TestHappyPathTransitions:
    """AT-01 and the full §5 arrow-chain, one hop at a time."""

    HAPPY_PATH = [
        CS.RECEIVED,
        CS.NORMALIZED,
        CS.ELIGIBILITY_CHECKED,
        CS.DIAGNOSED,
        CS.CANDIDATES_GENERATED,
        CS.ACTION_SCORED,
        CS.DECISION_PENDING,
        CS.VALIDATED,
        CS.ACTION_SCHEDULED,
        CS.ACTION_EXECUTED,
        CS.AWAITING_OUTCOME,
        CS.RECOVERED,
    ]

    def test_full_happy_path_executes_in_order(self, case_factory, now):
        case = case_factory(CS.RECEIVED)
        for to_state in self.HAPPY_PATH[1:]:
            case, audit_event = transition(
                case,
                to_state,
                actor_type=ActorType.POLICY_ENGINE,
                actor_version="test-v1",
                now=now,
            )
            assert case.state == to_state
            assert audit_event.new_state == to_state
            assert audit_event.event_type == "STATE_TRANSITION"
        assert case.state == CS.RECOVERED

    def test_updated_at_advances_on_transition(self, case_factory):
        from datetime import datetime, timedelta, timezone

        t0 = datetime(2026, 8, 23, 10, 30, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(minutes=5)
        case = case_factory(CS.RECEIVED, updated_at=t0)
        new_case, _ = transition(
            case,
            CS.NORMALIZED,
            actor_type=ActorType.INGESTION_SERVICE,
            actor_version="ingest-v1",
            now=t1,
        )
        assert new_case.updated_at == t1
        # Original case object is untouched (immutability of the transition op).
        assert case.state == CS.RECEIVED

    def test_audit_event_records_previous_and_new_state(self, case_factory, now):
        case = case_factory(CS.RECEIVED)
        _, audit_event = transition(
            case,
            CS.NORMALIZED,
            actor_type=ActorType.INGESTION_SERVICE,
            actor_version="ingest-v1",
            reason_codes=("SCHEMA_VALID",),
            now=now,
        )
        assert audit_event.previous_state == CS.RECEIVED
        assert audit_event.new_state == CS.NORMALIZED
        assert audit_event.reason_codes == ("SCHEMA_VALID",)
        assert audit_event.case_id == case.case_id


class TestEligibilityStopScenarios:
    """docs/demo-script.md Cases 4, 5, 6."""

    def test_opt_out_stop_from_normalized(self, case_factory, now):
        case = case_factory(CS.NORMALIZED, opted_out=True)
        new_case, audit_event = transition(
            case,
            CS.CUSTOMER_OPTED_OUT,
            actor_type=ActorType.ELIGIBILITY_ENGINE,
            actor_version="eligibility-v1",
            reason_codes=("CUSTOMER_OPTED_OUT",),
            now=now,
        )
        assert new_case.state == CS.CUSTOMER_OPTED_OUT

    def test_dispute_stop_from_normalized(self, case_factory, now):
        case = case_factory(CS.NORMALIZED, disputed=True)
        new_case, _ = transition(
            case,
            CS.CUSTOMER_DISPUTED,
            actor_type=ActorType.ELIGIBILITY_ENGINE,
            actor_version="eligibility-v1",
            reason_codes=("CUSTOMER_DISPUTED",),
            now=now,
        )
        assert new_case.state == CS.CUSTOMER_DISPUTED

    def test_contact_cap_stop_from_eligibility_checked(self, case_factory, now):
        case = case_factory(CS.ELIGIBILITY_CHECKED, contact_count_week=2)
        new_case, audit_event = transition(
            case,
            CS.STOPPED_BY_POLICY,
            actor_type=ActorType.ELIGIBILITY_ENGINE,
            actor_version="eligibility-v1",
            reason_codes=("CONTACT_CAP_REACHED",),
            now=now,
        )
        assert new_case.state == CS.STOPPED_BY_POLICY
        assert audit_event.reason_codes == ("CONTACT_CAP_REACHED",)

    def test_opted_out_case_cannot_reach_active_state(self, case_factory, now):
        case = case_factory(CS.CUSTOMER_OPTED_OUT)
        with pytest.raises(InvalidStateTransition):
            transition(
                case,
                CS.CANDIDATES_GENERATED,
                actor_type=ActorType.POLICY_ENGINE,
                actor_version="policy-v1",
                now=now,
            )

    def test_disputed_case_cannot_execute_new_action(self, case_factory, now):
        case = case_factory(CS.CUSTOMER_DISPUTED)
        with pytest.raises(InvalidStateTransition):
            transition(
                case,
                CS.ACTION_SCHEDULED,
                actor_type=ActorType.EXECUTOR,
                actor_version="executor-v1",
                now=now,
            )


class TestPolicyEngineOutcomes:
    """AT-11: no eligible candidates / below economic threshold."""

    def test_no_eligible_candidates_stops_pursuit(self, case_factory, now):
        case = case_factory(CS.CANDIDATES_GENERATED)
        new_case, _ = transition(
            case,
            CS.STOPPED_BY_POLICY,
            actor_type=ActorType.POLICY_ENGINE,
            actor_version="policy-v1",
            reason_codes=("NO_NON_CONTACT_CANDIDATES_ELIGIBLE",),
            now=now,
        )
        assert new_case.state == CS.STOPPED_BY_POLICY

    def test_below_economic_threshold_stops_pursuit(self, case_factory, now):
        case = case_factory(CS.ACTION_SCORED)
        new_case, audit_event = transition(
            case,
            CS.STOPPED_BY_POLICY,
            actor_type=ActorType.POLICY_ENGINE,
            actor_version="policy-v1",
            reason_codes=("BELOW_ECONOMIC_THRESHOLD",),
            now=now,
        )
        assert new_case.state == CS.STOPPED_BY_POLICY
        assert audit_event.reason_codes == ("BELOW_ECONOMIC_THRESHOLD",)

    def test_actionable_candidate_proceeds_to_decision_pending(self, case_factory, now):
        case = case_factory(CS.ACTION_SCORED)
        new_case, _ = transition(
            case,
            CS.DECISION_PENDING,
            actor_type=ActorType.POLICY_ENGINE,
            actor_version="policy-v1",
            now=now,
        )
        assert new_case.state == CS.DECISION_PENDING


class TestLLMValidationAndFallback:
    """AT-12: invalid LLM response -> deterministic fallback or escalation."""

    def test_valid_llm_response_reaches_validated(self, case_factory, now):
        case = case_factory(CS.DECISION_PENDING)
        new_case, _ = transition(
            case,
            CS.VALIDATED,
            actor_type=ActorType.VALIDATOR,
            actor_version="validator-v1",
            now=now,
        )
        assert new_case.state == CS.VALIDATED

    def test_invalid_llm_response_falls_back_to_validated(self, case_factory, now):
        # Deterministic-fallback strategy: still reaches VALIDATED, but the
        # audit trail records that a fallback was used.
        case = case_factory(CS.DECISION_PENDING)
        new_case, audit_event = transition(
            case,
            CS.VALIDATED,
            actor_type=ActorType.VALIDATOR,
            actor_version="validator-v1",
            reason_codes=("LLM_RESPONSE_REJECTED", "FALLBACK_USED"),
            now=now,
        )
        assert new_case.state == CS.VALIDATED
        assert "FALLBACK_USED" in audit_event.reason_codes

    def test_invalid_llm_response_can_escalate_to_human(self, case_factory, now):
        # escalate_to_human fallback strategy (policy-decisions.md §15)
        case = case_factory(CS.DECISION_PENDING)
        new_case, _ = transition(
            case,
            CS.ESCALATED_TO_HUMAN,
            actor_type=ActorType.VALIDATOR,
            actor_version="validator-v1",
            reason_codes=("LLM_RESPONSE_REJECTED",),
            now=now,
        )
        assert new_case.state == CS.ESCALATED_TO_HUMAN

    def test_llm_cannot_jump_case_directly_to_executed(self, case_factory, now):
        case = case_factory(CS.DECISION_PENDING)
        with pytest.raises(InvalidStateTransition):
            transition(
                case,
                CS.ACTION_EXECUTED,
                actor_type=ActorType.LLM,
                actor_version="llm-v1",
                now=now,
            )


class TestEscalationCandidate:
    """docs/demo-script.md Case 3: expired card -> escalate_to_human."""

    def test_validated_escalation_candidate_goes_to_escalated(self, case_factory, now):
        case = case_factory(CS.VALIDATED)
        new_case, _ = transition(
            case,
            CS.ESCALATED_TO_HUMAN,
            actor_type=ActorType.EXECUTOR,
            actor_version="executor-v1",
            now=now,
        )
        assert new_case.state == CS.ESCALATED_TO_HUMAN

    def test_validated_normal_candidate_goes_to_scheduled(self, case_factory, now):
        case = case_factory(CS.VALIDATED)
        new_case, _ = transition(
            case,
            CS.ACTION_SCHEDULED,
            actor_type=ActorType.SCHEDULER,
            actor_version="scheduler-v1",
            now=now,
        )
        assert new_case.state == CS.ACTION_SCHEDULED


class TestOutcomeReconciliation:
    """AT-14a/b/c/d."""

    def test_success_after_action_recovers_case(self, case_factory, now):
        case = case_factory(CS.AWAITING_OUTCOME)
        new_case, audit_event = transition(
            case,
            CS.RECOVERED,
            actor_type=ActorType.OUTCOME_RECONCILER,
            actor_version="reconciler-v1",
            reason_codes=("PAYMENT_SUCCESS",),
            now=now,
        )
        assert new_case.state == CS.RECOVERED

    def test_out_of_order_success_recovers_case_from_scheduled(self, case_factory, now):
        # AT-14b: success arrives while action is only ACTION_SCHEDULED
        # (not yet executed). The success is authoritative.
        case = case_factory(CS.ACTION_SCHEDULED)
        new_case, audit_event = transition(
            case,
            CS.RECOVERED,
            actor_type=ActorType.OUTCOME_RECONCILER,
            actor_version="reconciler-v1",
            reason_codes=("OUT_OF_ORDER_PAYMENT_SUCCESS", "PENDING_ACTION_CANCELED"),
            now=now,
        )
        assert new_case.state == CS.RECOVERED

    def test_failure_after_action_moves_to_failed_execution(self, case_factory, now):
        case = case_factory(CS.AWAITING_OUTCOME)
        new_case, audit_event = transition(
            case,
            CS.FAILED_EXECUTION,
            actor_type=ActorType.OUTCOME_RECONCILER,
            actor_version="reconciler-v1",
            reason_codes=("PAYMENT_FAILURE",),
            now=now,
        )
        assert new_case.state == CS.FAILED_EXECUTION

    def test_failed_execution_can_return_to_diagnosed_for_redo(self, case_factory, now):
        case = case_factory(CS.FAILED_EXECUTION)
        new_case, _ = transition(
            case,
            CS.DIAGNOSED,
            actor_type=ActorType.POLICY_ENGINE,
            actor_version="policy-v1",
            now=now,
        )
        assert new_case.state == CS.DIAGNOSED

    def test_already_recovered_case_rejects_new_action_request(self, case_factory, now):
        # AT-14d: the state machine must reject the transition outright.
        case = case_factory(CS.RECOVERED)
        with pytest.raises(InvalidStateTransition) as exc_info:
            transition(
                case,
                CS.ACTION_SCHEDULED,
                actor_type=ActorType.SCHEDULER,
                actor_version="scheduler-v1",
                now=now,
            )
        assert exc_info.value.case_id == case.case_id
        assert exc_info.value.from_state == CS.RECOVERED
        assert exc_info.value.to_state == CS.ACTION_SCHEDULED


class TestHoldoutNaturalRecovery:
    """docs/demo-script.md Case 9: holdout case stopped, later pays naturally."""

    def test_stopped_holdout_case_can_be_recovered_naturally(self, case_factory, now):
        case = case_factory(CS.STOPPED_BY_POLICY, cohort=None)
        new_case, audit_event = transition(
            case,
            CS.RECOVERED,
            actor_type=ActorType.OUTCOME_RECONCILER,
            actor_version="reconciler-v1",
            reason_codes=("HOLDOUT_NATURAL_PAYMENT",),
            now=now,
        )
        assert new_case.state == CS.RECOVERED

    def test_stopped_case_cannot_jump_to_an_active_state(self, case_factory, now):
        case = case_factory(CS.STOPPED_BY_POLICY)
        with pytest.raises(InvalidStateTransition):
            transition(
                case,
                CS.CANDIDATES_GENERATED,
                actor_type=ActorType.POLICY_ENGINE,
                actor_version="policy-v1",
                now=now,
            )


class TestExpiry:
    @pytest.mark.parametrize(
        "state",
        [
            CS.NORMALIZED,
            CS.ELIGIBILITY_CHECKED,
            CS.DIAGNOSED,
            CS.CANDIDATES_GENERATED,
            CS.ACTION_SCORED,
            CS.DECISION_PENDING,
            CS.VALIDATED,
            CS.ACTION_SCHEDULED,
            CS.AWAITING_OUTCOME,
        ],
    )
    def test_any_in_flight_case_can_expire(self, case_factory, now, state):
        case = case_factory(state)
        new_case, _ = transition(
            case,
            CS.EXPIRED,
            actor_type=ActorType.SCHEDULER,
            actor_version="scheduler-v1",
            reason_codes=("CASE_EXPIRY_ELAPSED",),
            now=now,
        )
        assert new_case.state == CS.EXPIRED

    def test_expired_case_is_terminal(self, case_factory, now):
        case = case_factory(CS.EXPIRED)
        with pytest.raises(InvalidStateTransition):
            transition(
                case,
                CS.DIAGNOSED,
                actor_type=ActorType.POLICY_ENGINE,
                actor_version="policy-v1",
                now=now,
            )


class TestArchitectureExamples:
    """The two explicit examples from docs/revised-architecture.md §5:

    "a case in RECOVERED cannot transition back to ACTION_SCHEDULED, and a
    case in CUSTOMER_DISPUTED cannot execute a new recovery action."
    """

    def test_recovered_cannot_go_back_to_action_scheduled(self, case_factory, now):
        case = case_factory(CS.RECOVERED)
        with pytest.raises(InvalidStateTransition):
            transition(
                case,
                CS.ACTION_SCHEDULED,
                actor_type=ActorType.SCHEDULER,
                actor_version="scheduler-v1",
                now=now,
            )

    def test_disputed_cannot_execute_new_recovery_action(self, case_factory, now):
        case = case_factory(CS.CUSTOMER_DISPUTED)
        with pytest.raises(InvalidStateTransition):
            transition(
                case,
                CS.ACTION_EXECUTED,
                actor_type=ActorType.EXECUTOR,
                actor_version="executor-v1",
                now=now,
            )


class TestNoSkippingAhead:
    """Backward and skip-ahead transitions must be rejected even though the
    endpoints are individually reachable via the correct path."""

    def test_cannot_skip_from_received_to_diagnosed(self, case_factory, now):
        case = case_factory(CS.RECEIVED)
        with pytest.raises(InvalidStateTransition):
            transition(
                case,
                CS.DIAGNOSED,
                actor_type=ActorType.DIAGNOSIS_MODEL,
                actor_version="diagnosis-v1",
                now=now,
            )

    def test_cannot_go_backward_from_validated_to_diagnosed(self, case_factory, now):
        case = case_factory(CS.VALIDATED)
        with pytest.raises(InvalidStateTransition):
            transition(
                case,
                CS.DIAGNOSED,
                actor_type=ActorType.DIAGNOSIS_MODEL,
                actor_version="diagnosis-v1",
                now=now,
            )

    def test_cannot_skip_eligibility_checked_straight_to_action_scheduled(
        self, case_factory, now
    ):
        case = case_factory(CS.ELIGIBILITY_CHECKED)
        with pytest.raises(InvalidStateTransition):
            transition(
                case,
                CS.ACTION_SCHEDULED,
                actor_type=ActorType.SCHEDULER,
                actor_version="scheduler-v1",
                now=now,
            )
