"""Eligibility checks (docs/revised-architecture.md §8.1).

Two distinct layers, matching the acceptance tests exactly:

  1. Absolute case-level stops (AT-04, AT-05; legal/account hold and
     subscription-status are the same shape). These happen BEFORE any
     candidate is generated -- the case never reaches
     CANDIDATES_GENERATED at all.

  2. Candidate-level gating (AT-06, AT-07, AT-08, plus root-cause
     action-eligibility and the unknown-decline fallback). These run
     once per fixed candidate during generation and only ever narrow
     which candidates are eligible -- they never by themselves stop the
     whole case (whether the case then stops is a candidates.py /
     engine.py decision: "STOPPED_BY_POLICY if no non-contact
     candidates are eligible", AT-07 #2).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from domain.enums import CaseState, DeclineCode
from domain.models import EligibilityResult, RecoveryCase

from .config import (
    CONTACT_CANDIDATE_KEYS,
    ESCALATE,
    PolicyConfig,
    RETRY_CANDIDATE_KEYS,
    STOP_PURSUIT,
)

# Root causes for which an automated payment retry can never succeed and
# must not be attempted (docs/build-runbook.md deterministic diagnosis
# map; docs/policy-decisions.md §13 note on expired_card/stolen_card).
RETRY_INELIGIBLE_ROOT_CAUSES = frozenset({DeclineCode.EXPIRED_CARD, DeclineCode.STOLEN_CARD})

# stolen_card: "Automated recovery likely inappropriate" (policy-decisions
# .md §13) -- no automated customer-facing action of any kind, only
# escalation or stopping.
FULLY_MANUAL_ROOT_CAUSES = frozenset({DeclineCode.STOLEN_CARD})


@dataclass(frozen=True)
class CaseStopResult:
    """An absolute, case-wide stop discovered before candidate generation."""

    reason_code: str
    target_state: CaseState


# Priority order matters when multiple flags are true simultaneously --
# first match wins, mirroring docs/revised-architecture.md §8.1's table
# order (opt-out, dispute, legal/account hold, ..., subscription status).
def check_case_level_stop(case: RecoveryCase, config: PolicyConfig) -> CaseStopResult | None:
    if case.opted_out and config.stop_on_opt_out:
        return CaseStopResult("CUSTOMER_OPTED_OUT", CaseState.CUSTOMER_OPTED_OUT)
    if case.disputed and config.stop_on_dispute:
        return CaseStopResult("CUSTOMER_DISPUTED", CaseState.CUSTOMER_DISPUTED)
    if case.legal_hold:
        # "Block action and route to an operator" (§8.1). Modeled as
        # STOPPED_BY_POLICY: no further automation, needs a human queue --
        # the same terminal-ish bucket used elsewhere for "stop pursuing
        # automatically, a human handles what's next."
        return CaseStopResult("LEGAL_OR_ACCOUNT_HOLD", CaseState.STOPPED_BY_POLICY)
    if case.subscription_canceled and config.stop_on_canceled_subscription:
        return CaseStopResult("SUBSCRIPTION_CANCELED", CaseState.STOPPED_BY_POLICY)
    if case.already_paid and config.stop_on_paid_subscription:
        return CaseStopResult("SUBSCRIPTION_ALREADY_PAID", CaseState.STOPPED_BY_POLICY)
    return None


def _cooldown_active(case: RecoveryCase, config: PolicyConfig, now: datetime) -> bool:
    if case.last_contact_at is None:
        return False
    elapsed = now - case.last_contact_at
    return elapsed < timedelta(hours=config.cooldown_hours_between_contacts)


def check_candidate_eligibility(
    candidate_key: str,
    case: RecoveryCase,
    config: PolicyConfig,
    root_cause: DeclineCode,
    now: datetime,
) -> EligibilityResult:
    """Per-candidate gating. Called once per fixed candidate key during
    generation. Never raises -- always returns an EligibilityResult."""

    # stop_pursuit is the universal fallback: always available.
    if candidate_key == STOP_PURSUIT:
        return EligibilityResult(allowed=True, reason_codes=("BASELINE_STOP_AVAILABLE",))

    # escalate_to_human: always available as an escalation path -- it is
    # precisely the unknown-decline / manual-only fallback target, so it
    # must never itself be blocked by those same rules.
    if candidate_key == ESCALATE:
        return EligibilityResult(allowed=True, reason_codes=("BASELINE_ESCALATION_AVAILABLE",))

    # Unknown-decline fallback: route everything except escalate/stop to
    # a human or a full stop (docs/policy-decisions.md §8).
    if root_cause == DeclineCode.UNKNOWN and config.escalate_on_unknown_decline:
        return EligibilityResult(allowed=False, reason_codes=("UNKNOWN_DECLINE_FALLBACK",))

    # Root-cause based action eligibility.
    if candidate_key in RETRY_CANDIDATE_KEYS and root_cause in RETRY_INELIGIBLE_ROOT_CAUSES:
        return EligibilityResult(allowed=False, reason_codes=("NOT_APPLICABLE_FOR_ROOT_CAUSE",))
    if candidate_key in CONTACT_CANDIDATE_KEYS and root_cause in FULLY_MANUAL_ROOT_CAUSES:
        return EligibilityResult(allowed=False, reason_codes=("NOT_APPLICABLE_FOR_ROOT_CAUSE",))

    # Retry cap (AT-08).
    if candidate_key in RETRY_CANDIDATE_KEYS:
        if case.retry_count_episode >= config.max_payment_retries_per_episode:
            return EligibilityResult(allowed=False, reason_codes=("RETRY_CAP_REACHED",))
        return EligibilityResult(allowed=True)

    # Contact cap + cooldown (AT-06, AT-07). Both apply only to
    # customer-facing contact candidates.
    if candidate_key in CONTACT_CANDIDATE_KEYS:
        if case.contact_count_week >= config.max_customer_contacts_per_week:
            return EligibilityResult(allowed=False, reason_codes=("CONTACT_CAP_REACHED",))
        if _cooldown_active(case, config, now):
            return EligibilityResult(allowed=False, reason_codes=("COOLDOWN_ACTIVE",))
        return EligibilityResult(allowed=True)

    raise ValueError(f"unrecognized candidate_key: {candidate_key!r}")  # pragma: no cover
