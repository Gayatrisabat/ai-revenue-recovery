"""Typed domain models.

One dataclass per table in docs/revised-architecture.md §13 (MVP data
tables) / §4 (canonical domain model), plus PolicyVersion for §14 of
docs/policy-decisions.md.

These are plain, framework-free dataclasses: no ORM, no DB driver, no
external API client. Validation is limited to structural/invariant
checks that are cheap and unambiguous (non-empty ids, non-negative
amounts, probabilities in [0, 1]); business-rule enforcement (contact
caps, cooldowns, economic thresholds, etc.) belongs to the policy engine,
which is out of scope for this change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .enums import (
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
from .errors import ValidationError


def _require_non_empty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must be a non-empty string")


def _require_non_negative(value: int, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValidationError(f"{field_name} must be a non-negative integer")


def _require_unit_interval(value: float, field_name: str) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValidationError(f"{field_name} must be a number")
    if not (0.0 <= float(value) <= 1.0):
        raise ValidationError(f"{field_name} must be between 0.0 and 1.0")


# ---------------------------------------------------------------------------
# 1. Raw event (docs/revised-architecture.md §4.1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawEvent:
    """Immutable, as-received payload. Never mutated after persistence."""

    event_id: str
    event_type: str
    occurred_at: datetime
    source: str
    customer_id: str
    subscription_id: str
    amount_minor: int
    currency: Currency
    decline_code: DeclineCode
    payment_method_type: str
    payment_method_fingerprint: str
    attempt_number: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for f in (
            "event_id",
            "event_type",
            "source",
            "customer_id",
            "subscription_id",
            "payment_method_type",
            "payment_method_fingerprint",
        ):
            _require_non_empty(getattr(self, f), f)
        _require_non_negative(self.amount_minor, "amount_minor")
        if self.attempt_number < 1:
            raise ValidationError("attempt_number must be >= 1")


# ---------------------------------------------------------------------------
# 2. Normalized event
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NormalizedEvent:
    """Canonical-schema event derived from a RawEvent.

    Kept distinct from RawEvent per §4.1: "The raw event is immutable.
    Normalized records may be enriched, but the original payload must
    remain available for replay and audit."
    """

    raw_event_id: str
    case_id: str
    customer_id: str
    subscription_id: str
    amount_minor: int
    currency: Currency
    decline_code: DeclineCode
    normalized_at: datetime
    schema_version: str = "v1"

    def __post_init__(self) -> None:
        for f in ("raw_event_id", "case_id", "customer_id", "subscription_id"):
            _require_non_empty(getattr(self, f), f)
        _require_non_negative(self.amount_minor, "amount_minor")


# ---------------------------------------------------------------------------
# 3. Recovery case (docs/revised-architecture.md §4.2)
# ---------------------------------------------------------------------------


@dataclass
class RecoveryCase:
    """The complete lifecycle record for one customer/subscription episode.

    Mutable by design (state, counters, timestamps change over the case's
    life) but state changes must only ever happen through
    state_machine.transition(), never by assigning `.state` directly.
    """

    case_id: str
    customer_id: str
    subscription_id: str
    principal_amount_minor: int
    currency: Currency
    state: CaseState
    created_at: datetime
    updated_at: datetime
    cohort: Cohort | None = None
    risk_tier: RiskTier = RiskTier.STANDARD
    contact_count_week: int = 0
    retry_count_episode: int = 0
    last_contact_at: datetime | None = None
    opted_out: bool = False
    disputed: bool = False
    policy_version: str | None = None
    model_version: str | None = None

    def __post_init__(self) -> None:
        for f in ("case_id", "customer_id", "subscription_id"):
            _require_non_empty(getattr(self, f), f)
        _require_non_negative(self.principal_amount_minor, "principal_amount_minor")
        _require_non_negative(self.contact_count_week, "contact_count_week")
        _require_non_negative(self.retry_count_episode, "retry_count_episode")


# ---------------------------------------------------------------------------
# 4. Diagnosis (docs/revised-architecture.md §7)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Diagnosis:
    diagnosis_id: str
    case_id: str
    root_cause: DeclineCode
    success_probability_now: float
    recommended_retry_window: str
    reason_codes: tuple[str, ...]
    confidence: float
    model_version: str
    created_at: datetime

    def __post_init__(self) -> None:
        for f in ("diagnosis_id", "case_id", "recommended_retry_window", "model_version"):
            _require_non_empty(getattr(self, f), f)
        _require_unit_interval(self.success_probability_now, "success_probability_now")
        _require_unit_interval(self.confidence, "confidence")


# ---------------------------------------------------------------------------
# 5. Action candidate (docs/revised-architecture.md §4.3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EligibilityResult:
    allowed: bool
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Economics:
    success_probability: float
    expected_gross_recovery_minor: int
    estimated_action_cost_minor: int
    risk_penalty_minor: int
    expected_net_recovery_minor: int

    def __post_init__(self) -> None:
        _require_unit_interval(self.success_probability, "success_probability")
        _require_non_negative(self.expected_gross_recovery_minor, "expected_gross_recovery_minor")
        _require_non_negative(self.estimated_action_cost_minor, "estimated_action_cost_minor")
        _require_non_negative(self.risk_penalty_minor, "risk_penalty_minor")


@dataclass(frozen=True)
class ActionCandidate:
    """Created only by deterministic policy code, never by the LLM."""

    candidate_id: str
    case_id: str
    action_type: ActionType
    parameters: dict[str, Any]
    eligibility: EligibilityResult
    economics: Economics

    def __post_init__(self) -> None:
        for f in ("candidate_id", "case_id"):
            _require_non_empty(getattr(self, f), f)


# ---------------------------------------------------------------------------
# 6. LLM decision (docs/revised-architecture.md §9.2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LLMDecision:
    """Bounded structured output. The LLM selects; it never invents."""

    decision_id: str
    case_id: str
    selected_candidate_id: str
    message_template_id: str | None
    personalization_variables: dict[str, str]
    decision_reason: str
    confidence: float
    is_valid: bool
    used_fallback: bool
    created_at: datetime
    rejection_reason: str | None = None

    def __post_init__(self) -> None:
        for f in ("decision_id", "case_id", "selected_candidate_id"):
            _require_non_empty(getattr(self, f), f)
        _require_unit_interval(self.confidence, "confidence")
        if not self.is_valid and not self.rejection_reason:
            raise ValidationError("rejection_reason is required when is_valid is False")


# ---------------------------------------------------------------------------
# 7. Execution (docs/revised-architecture.md §10)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Execution:
    execution_id: str
    case_id: str
    candidate_id: str
    idempotency_key: str
    policy_version: str
    approved_at: datetime

    def __post_init__(self) -> None:
        for f in (
            "execution_id",
            "case_id",
            "candidate_id",
            "idempotency_key",
            "policy_version",
        ):
            _require_non_empty(getattr(self, f), f)


# ---------------------------------------------------------------------------
# 8. Outcome
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Outcome:
    outcome_id: str
    case_id: str
    execution_id: str | None
    outcome_type: OutcomeType
    occurred_at: datetime
    reconciled: bool = False
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        for f in ("outcome_id", "case_id"):
            _require_non_empty(getattr(self, f), f)


# ---------------------------------------------------------------------------
# 9. Revenue ledger entry (docs/revised-architecture.md §11.2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RevenueLedgerEntry:
    ledger_entry_id: str
    case_id: str
    cohort: Cohort
    amount_recovered_minor: int
    recovered_at: datetime
    action_id: str | None
    attribution_window_hours: int
    attribution_status: AttributionStatus
    action_cost_minor: int = 0

    def __post_init__(self) -> None:
        for f in ("ledger_entry_id", "case_id"):
            _require_non_empty(getattr(self, f), f)
        _require_non_negative(self.amount_recovered_minor, "amount_recovered_minor")
        _require_non_negative(self.attribution_window_hours, "attribution_window_hours")
        _require_non_negative(self.action_cost_minor, "action_cost_minor")
        if (
            self.attribution_status == AttributionStatus.HOLDOUT_RECOVERY
            and self.cohort != Cohort.HOLDOUT
        ):
            raise ValidationError(
                "attribution_status=holdout_recovery requires cohort=holdout"
            )


# ---------------------------------------------------------------------------
# 10. Audit event (docs/revised-architecture.md §12)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditEvent:
    """Append-only. Must never be mutated or deleted once created."""

    audit_id: str
    case_id: str
    event_type: str
    actor_type: ActorType
    actor_version: str
    payload: dict[str, Any]
    created_at: datetime
    previous_state: CaseState | None = None
    new_state: CaseState | None = None
    reason_codes: tuple[str, ...] = ()
    policy_version: str | None = None
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        for f in ("audit_id", "case_id", "event_type", "actor_version"):
            _require_non_empty(getattr(self, f), f)


# ---------------------------------------------------------------------------
# 11. Policy version (docs/policy-decisions.md)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyVersion:
    """A single, immutable, approved snapshot of policy configuration.

    Every field mirrors an `__APPROVE__` row in docs/policy-decisions.md.
    A new PolicyVersion must be created (never mutated) whenever any value
    changes, per that document's header: "Every policy change must
    increment the version."
    """

    policy_version: str
    max_payment_retries_per_episode: int
    max_customer_contacts_per_week: int
    cooldown_hours_between_contacts: int
    high_value_approval_threshold_minor: int
    minimum_expected_net_recovery_minor: int
    stop_on_opt_out: bool
    stop_on_dispute: bool
    stop_on_canceled_subscription: bool
    stop_on_already_paid: bool
    escalate_on_unknown_decline: bool
    treatment_cohort_percentage: int
    holdout_cohort_percentage: int
    attribution_window_hours: int
    approved_at: datetime
    approved_by: str

    def __post_init__(self) -> None:
        for f in ("policy_version", "approved_by"):
            _require_non_empty(getattr(self, f), f)
        if not self.stop_on_opt_out:
            raise ValidationError(
                "stop_on_opt_out must be true (docs/policy-decisions.md §7)"
            )
        if not self.stop_on_dispute:
            raise ValidationError(
                "stop_on_dispute must be true (docs/policy-decisions.md §7)"
            )
        if self.treatment_cohort_percentage + self.holdout_cohort_percentage != 100:
            raise ValidationError(
                "treatment_cohort_percentage + holdout_cohort_percentage must equal 100"
            )
        for f in (
            "max_payment_retries_per_episode",
            "max_customer_contacts_per_week",
            "cooldown_hours_between_contacts",
            "high_value_approval_threshold_minor",
            "minimum_expected_net_recovery_minor",
            "attribution_window_hours",
        ):
            _require_non_negative(getattr(self, f), f)
