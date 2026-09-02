"""Enumerations shared across the domain model.

Values are taken verbatim from docs/revised-architecture.md (§5 State
machine, §4 Canonical domain model, §8 Policy engine outcomes, §11
Incremental revenue measurement) and docs/acceptance-tests.md.
"""

from __future__ import annotations

from enum import Enum


class CaseState(str, Enum):
    """Every state a RecoveryCase can occupy.

    Ordering here mirrors docs/revised-architecture.md §5 for readability
    only; the state_machine module is the single source of truth for
    which transitions are actually legal.
    """

    # --- Active (non-terminal) states -----------------------------------
    RECEIVED = "RECEIVED"
    NORMALIZED = "NORMALIZED"
    ELIGIBILITY_CHECKED = "ELIGIBILITY_CHECKED"
    DIAGNOSED = "DIAGNOSED"
    CANDIDATES_GENERATED = "CANDIDATES_GENERATED"
    ACTION_SCORED = "ACTION_SCORED"
    DECISION_PENDING = "DECISION_PENDING"
    VALIDATED = "VALIDATED"
    ACTION_SCHEDULED = "ACTION_SCHEDULED"
    ACTION_EXECUTED = "ACTION_EXECUTED"
    AWAITING_OUTCOME = "AWAITING_OUTCOME"

    # --- Terminal states ---------------------------------------------------
    RECOVERED = "RECOVERED"
    STOPPED_BY_POLICY = "STOPPED_BY_POLICY"
    ESCALATED_TO_HUMAN = "ESCALATED_TO_HUMAN"
    CUSTOMER_OPTED_OUT = "CUSTOMER_OPTED_OUT"
    CUSTOMER_DISPUTED = "CUSTOMER_DISPUTED"
    EXPIRED = "EXPIRED"
    FAILED_EXECUTION = "FAILED_EXECUTION"


# States from which no further automated transition is possible.
# NOTE: STOPPED_BY_POLICY and FAILED_EXECUTION are intentionally *not*
# fully terminal in the transition graph: docs/demo-script.md Case 9 and
# docs/acceptance-tests.md AT-14c both describe a stopped/failed case
# later reconciling to RECOVERED or DIAGNOSED. They are still terminal
# in the sense that no *new recovery action* may be scheduled from them.
TERMINAL_STATES = frozenset(
    {
        CaseState.RECOVERED,
        CaseState.ESCALATED_TO_HUMAN,
        CaseState.CUSTOMER_OPTED_OUT,
        CaseState.CUSTOMER_DISPUTED,
        CaseState.EXPIRED,
    }
)


class Cohort(str, Enum):
    TREATMENT = "treatment"
    HOLDOUT = "holdout"


class RiskTier(str, Enum):
    STANDARD = "standard"
    ELEVATED = "elevated"
    HIGH = "high"


class Currency(str, Enum):
    INR = "INR"
    USD = "USD"


class ActionType(str, Enum):
    RETRY_PAYMENT = "retry_payment"
    SEND_APPROVED_EMAIL_TEMPLATE = "send_approved_email_template_01"
    OFFER_APPROVED_ALTERNATE_METHOD = "offer_approved_alternate_payment_method"
    ESCALATE_TO_HUMAN = "escalate_to_human"
    STOP_PURSUIT = "stop_pursuit"


class CandidateEngineOutcome(str, Enum):
    """The three outcomes the policy/economic engine may return (§8.3)."""

    ACTIONABLE_CANDIDATES = "ACTIONABLE_CANDIDATES"
    NO_ACTION_ABOVE_THRESHOLD = "NO_ACTION_ABOVE_THRESHOLD"
    BLOCKED_BY_POLICY = "BLOCKED_BY_POLICY"


class AttributionStatus(str, Enum):
    OBSERVED_AFTER_ACTION = "observed_after_action"
    HOLDOUT_RECOVERY = "holdout_recovery"


class ActorType(str, Enum):
    """Who/what performed the state transition, for the audit trail."""

    INGESTION_SERVICE = "ingestion_service"
    ELIGIBILITY_ENGINE = "eligibility_engine"
    DIAGNOSIS_MODEL = "diagnosis_model"
    POLICY_ENGINE = "policy_engine"
    LLM = "llm"
    VALIDATOR = "validator"
    EXECUTOR = "executor"
    OUTCOME_RECONCILER = "outcome_reconciler"
    HUMAN_OPERATOR = "human_operator"
    SCHEDULER = "scheduler"


class OutcomeType(str, Enum):
    PAYMENT_SUCCESS = "payment_success"
    PAYMENT_FAILURE = "payment_failure"
    MESSAGE_DELIVERED = "message_delivered"
    MESSAGE_FAILED = "message_failed"
    CUSTOMER_RESPONSE = "customer_response"


class DeclineCode(str, Enum):
    INSUFFICIENT_FUNDS = "insufficient_funds"
    EXPIRED_CARD = "expired_card"
    ISSUER_DECLINE = "issuer_decline"
    NETWORK_ERROR = "network_error"
    DO_NOT_HONOR = "do_not_honor"
    STOLEN_CARD = "stolen_card"
    UNKNOWN = "unknown"
