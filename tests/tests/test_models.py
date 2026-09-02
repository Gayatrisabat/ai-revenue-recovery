from __future__ import annotations

from datetime import datetime, timezone

import pytest

from domain.enums import (
    ActionType,
    ActorType,
    AttributionStatus,
    CaseState,
    Cohort,
    Currency,
    DeclineCode,
    OutcomeType,
    RiskTier,
)
from domain.errors import ValidationError
from domain.models import (
    ActionCandidate,
    AuditEvent,
    Diagnosis,
    Economics,
    EligibilityResult,
    Execution,
    LLMDecision,
    NormalizedEvent,
    Outcome,
    PolicyVersion,
    RawEvent,
    RecoveryCase,
    RevenueLedgerEntry,
)

NOW = datetime(2026, 8, 23, 10, 30, 0, tzinfo=timezone.utc)


def make_raw_event(**overrides) -> RawEvent:
    defaults = dict(
        event_id="evt_01",
        event_type="subscription.payment_failed",
        occurred_at=NOW,
        source="mock_gateway",
        customer_id="cus_123",
        subscription_id="sub_456",
        amount_minor=149900,
        currency=Currency.INR,
        decline_code=DeclineCode.INSUFFICIENT_FUNDS,
        payment_method_type="card",
        payment_method_fingerprint="pm_fp_789",
        attempt_number=1,
    )
    defaults.update(overrides)
    return RawEvent(**defaults)


class TestRawEvent:
    def test_valid_construction(self):
        event = make_raw_event()
        assert event.event_id == "evt_01"
        assert event.amount_minor == 149900

    def test_immutable(self):
        event = make_raw_event()
        with pytest.raises(Exception):
            event.event_id = "evt_02"  # type: ignore[misc]

    @pytest.mark.parametrize("field_name", ["event_id", "customer_id", "subscription_id"])
    def test_rejects_empty_required_string(self, field_name):
        with pytest.raises(ValidationError):
            make_raw_event(**{field_name: ""})

    def test_rejects_negative_amount(self):
        with pytest.raises(ValidationError):
            make_raw_event(amount_minor=-1)

    def test_rejects_zero_attempt_number(self):
        with pytest.raises(ValidationError):
            make_raw_event(attempt_number=0)


class TestNormalizedEvent:
    def test_valid_construction(self):
        event = NormalizedEvent(
            raw_event_id="evt_01",
            case_id="case_001",
            customer_id="cus_123",
            subscription_id="sub_456",
            amount_minor=149900,
            currency=Currency.INR,
            decline_code=DeclineCode.INSUFFICIENT_FUNDS,
            normalized_at=NOW,
        )
        assert event.case_id == "case_001"

    def test_rejects_negative_amount(self):
        with pytest.raises(ValidationError):
            NormalizedEvent(
                raw_event_id="evt_01",
                case_id="case_001",
                customer_id="cus_123",
                subscription_id="sub_456",
                amount_minor=-5,
                currency=Currency.INR,
                decline_code=DeclineCode.UNKNOWN,
                normalized_at=NOW,
            )


class TestRecoveryCase:
    def test_valid_construction_defaults(self):
        case = RecoveryCase(
            case_id="case_001",
            customer_id="cus_123",
            subscription_id="sub_456",
            principal_amount_minor=149900,
            currency=Currency.INR,
            state=CaseState.RECEIVED,
            created_at=NOW,
            updated_at=NOW,
        )
        assert case.state == CaseState.RECEIVED
        assert case.cohort is None
        assert case.opted_out is False
        assert case.disputed is False
        assert case.risk_tier == RiskTier.STANDARD

    def test_rejects_negative_principal(self):
        with pytest.raises(ValidationError):
            RecoveryCase(
                case_id="case_001",
                customer_id="cus_123",
                subscription_id="sub_456",
                principal_amount_minor=-1,
                currency=Currency.INR,
                state=CaseState.RECEIVED,
                created_at=NOW,
                updated_at=NOW,
            )

    def test_mutable_fields_can_be_updated(self):
        case = RecoveryCase(
            case_id="case_001",
            customer_id="cus_123",
            subscription_id="sub_456",
            principal_amount_minor=149900,
            currency=Currency.INR,
            state=CaseState.RECEIVED,
            created_at=NOW,
            updated_at=NOW,
        )
        case.contact_count_week += 1
        assert case.contact_count_week == 1


class TestDiagnosis:
    def test_valid_construction(self):
        diagnosis = Diagnosis(
            diagnosis_id="diag_001",
            case_id="case_001",
            root_cause=DeclineCode.INSUFFICIENT_FUNDS,
            success_probability_now=0.18,
            recommended_retry_window="24_to_72_hours",
            reason_codes=("DECLINE_CODE_MATCH",),
            confidence=0.84,
            model_version="diagnosis_v1",
            created_at=NOW,
        )
        assert diagnosis.confidence == 0.84

    @pytest.mark.parametrize("value", [-0.01, 1.01])
    def test_rejects_out_of_range_probability(self, value):
        with pytest.raises(ValidationError):
            Diagnosis(
                diagnosis_id="diag_001",
                case_id="case_001",
                root_cause=DeclineCode.INSUFFICIENT_FUNDS,
                success_probability_now=value,
                recommended_retry_window="24_to_72_hours",
                reason_codes=(),
                confidence=0.5,
                model_version="diagnosis_v1",
                created_at=NOW,
            )


class TestActionCandidate:
    def _economics(self, **overrides) -> Economics:
        defaults = dict(
            success_probability=0.42,
            expected_gross_recovery_minor=62958,
            estimated_action_cost_minor=100,
            risk_penalty_minor=0,
            expected_net_recovery_minor=62858,
        )
        defaults.update(overrides)
        return Economics(**defaults)

    def test_valid_construction(self):
        candidate = ActionCandidate(
            candidate_id="cand_24h_retry",
            case_id="case_001",
            action_type=ActionType.RETRY_PAYMENT,
            parameters={"delay_hours": 24, "amount_minor": 149900},
            eligibility=EligibilityResult(allowed=True, reason_codes=("WITHIN_RETRY_BUDGET",)),
            economics=self._economics(),
        )
        assert candidate.action_type == ActionType.RETRY_PAYMENT

    def test_economics_rejects_negative_cost(self):
        with pytest.raises(ValidationError):
            self._economics(estimated_action_cost_minor=-1)


class TestLLMDecision:
    def test_valid_decision(self):
        decision = LLMDecision(
            decision_id="dec_001",
            case_id="case_001",
            selected_candidate_id="cand_email_template_01",
            message_template_id="email_template_01",
            personalization_variables={"customer_first_name": "Asha"},
            decision_reason="Reminder has higher expected value than retry.",
            confidence=0.88,
            is_valid=True,
            used_fallback=False,
            created_at=NOW,
        )
        assert decision.is_valid

    def test_invalid_decision_requires_rejection_reason(self):
        with pytest.raises(ValidationError):
            LLMDecision(
                decision_id="dec_001",
                case_id="case_001",
                selected_candidate_id="cand_email_template_01",
                message_template_id=None,
                personalization_variables={},
                decision_reason="",
                confidence=0.0,
                is_valid=False,
                used_fallback=True,
                created_at=NOW,
                rejection_reason=None,
            )

    def test_rejected_decision_with_reason_is_valid_object(self):
        decision = LLMDecision(
            decision_id="dec_002",
            case_id="case_001",
            selected_candidate_id="cand_unknown",
            message_template_id=None,
            personalization_variables={},
            decision_reason="",
            confidence=0.0,
            is_valid=False,
            used_fallback=True,
            created_at=NOW,
            rejection_reason="malformed_json",
        )
        assert decision.rejection_reason == "malformed_json"


class TestExecution:
    def test_valid_construction(self):
        execution = Execution(
            execution_id="exec_001",
            case_id="case_001",
            candidate_id="cand_24h_retry",
            idempotency_key="case_001:cand_24h_retry:v1",
            policy_version="policy_demo_v1",
            approved_at=NOW,
        )
        assert execution.idempotency_key == "case_001:cand_24h_retry:v1"

    def test_rejects_empty_idempotency_key(self):
        with pytest.raises(ValidationError):
            Execution(
                execution_id="exec_001",
                case_id="case_001",
                candidate_id="cand_24h_retry",
                idempotency_key="",
                policy_version="policy_demo_v1",
                approved_at=NOW,
            )


class TestOutcome:
    def test_valid_construction(self):
        outcome = Outcome(
            outcome_id="out_001",
            case_id="case_001",
            execution_id="exec_001",
            outcome_type=OutcomeType.PAYMENT_SUCCESS,
            occurred_at=NOW,
            reconciled=True,
        )
        assert outcome.outcome_type == OutcomeType.PAYMENT_SUCCESS


class TestRevenueLedgerEntry:
    def test_valid_treatment_entry(self):
        entry = RevenueLedgerEntry(
            ledger_entry_id="ledger_001",
            case_id="case_001",
            cohort=Cohort.TREATMENT,
            amount_recovered_minor=149900,
            recovered_at=NOW,
            action_id="action_001",
            attribution_window_hours=72,
            attribution_status=AttributionStatus.OBSERVED_AFTER_ACTION,
            action_cost_minor=100,
        )
        assert entry.attribution_status == AttributionStatus.OBSERVED_AFTER_ACTION

    def test_valid_holdout_entry(self):
        entry = RevenueLedgerEntry(
            ledger_entry_id="ledger_002",
            case_id="case_009",
            cohort=Cohort.HOLDOUT,
            amount_recovered_minor=149900,
            recovered_at=NOW,
            action_id=None,
            attribution_window_hours=72,
            attribution_status=AttributionStatus.HOLDOUT_RECOVERY,
        )
        assert entry.action_id is None

    def test_holdout_attribution_requires_holdout_cohort(self):
        with pytest.raises(ValidationError):
            RevenueLedgerEntry(
                ledger_entry_id="ledger_003",
                case_id="case_003",
                cohort=Cohort.TREATMENT,
                amount_recovered_minor=149900,
                recovered_at=NOW,
                action_id=None,
                attribution_window_hours=72,
                attribution_status=AttributionStatus.HOLDOUT_RECOVERY,
            )


class TestAuditEvent:
    def test_valid_construction(self):
        event = AuditEvent(
            audit_id="audit_001",
            case_id="case_001",
            event_type="ACTION_VALIDATED",
            actor_type=ActorType.POLICY_ENGINE,
            actor_version="policy-engine-v1",
            payload={"candidate_id": "cand_email_template_01"},
            created_at=NOW,
            previous_state=CaseState.DECISION_PENDING,
            new_state=CaseState.VALIDATED,
            reason_codes=("WITHIN_CONTACT_CAP",),
        )
        assert event.new_state == CaseState.VALIDATED

    def test_rejects_empty_event_type(self):
        with pytest.raises(ValidationError):
            AuditEvent(
                audit_id="audit_001",
                case_id="case_001",
                event_type="",
                actor_type=ActorType.POLICY_ENGINE,
                actor_version="policy-engine-v1",
                payload={},
                created_at=NOW,
            )


class TestPolicyVersion:
    def _policy(self, **overrides) -> PolicyVersion:
        defaults = dict(
            policy_version="policy_demo_v1",
            max_payment_retries_per_episode=3,
            max_customer_contacts_per_week=2,
            cooldown_hours_between_contacts=24,
            high_value_approval_threshold_minor=100000,
            minimum_expected_net_recovery_minor=500,
            stop_on_opt_out=True,
            stop_on_dispute=True,
            stop_on_canceled_subscription=True,
            stop_on_already_paid=True,
            escalate_on_unknown_decline=True,
            treatment_cohort_percentage=80,
            holdout_cohort_percentage=20,
            attribution_window_hours=72,
            approved_at=NOW,
            approved_by="business_owner",
        )
        defaults.update(overrides)
        return PolicyVersion(**defaults)

    def test_valid_construction(self):
        policy = self._policy()
        assert policy.policy_version == "policy_demo_v1"

    def test_stop_on_opt_out_must_be_true(self):
        with pytest.raises(ValidationError):
            self._policy(stop_on_opt_out=False)

    def test_stop_on_dispute_must_be_true(self):
        with pytest.raises(ValidationError):
            self._policy(stop_on_dispute=False)

    def test_cohort_percentages_must_sum_to_100(self):
        with pytest.raises(ValidationError):
            self._policy(treatment_cohort_percentage=70, holdout_cohort_percentage=20)
