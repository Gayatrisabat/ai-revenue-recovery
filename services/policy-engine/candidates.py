"""Candidate generation (docs/revised-architecture.md §8.2).

Every candidate's parameters (delay, amount, template id) are fixed
here, by deterministic code reading versioned policy configuration --
never by the LLM (AT-09 #1, #3; CLAUDE.md safety invariant). The amount
on every candidate is always the case's own
`principal_amount_minor` -- the "authorized recoverable amount" -- so
amount integrity (§8.1) holds by construction. `validate_amount_integrity`
is exposed separately so it can be exercised directly against a
tampered value in tests.
"""

from __future__ import annotations

from datetime import datetime

from domain.enums import ActionType, DeclineCode, RiskTier
from domain.models import ActionCandidate, RecoveryCase

from .config import (
    ALL_CANDIDATE_KEYS,
    ESCALATE,
    EconomicTables,
    OFFER_ALT_METHOD,
    PolicyConfig,
    RETRY_24H,
    RETRY_72H,
    SEND_EMAIL,
    STOP_PURSUIT,
)
from .eligibility import check_candidate_eligibility
from .errors import AmountIntegrityError
from .scoring import compute_economics

CANDIDATE_ACTION_TYPES: dict[str, ActionType] = {
    RETRY_24H: ActionType.RETRY_PAYMENT,
    RETRY_72H: ActionType.RETRY_PAYMENT,
    SEND_EMAIL: ActionType.SEND_APPROVED_EMAIL_TEMPLATE,
    OFFER_ALT_METHOD: ActionType.OFFER_APPROVED_ALTERNATE_METHOD,
    ESCALATE: ActionType.ESCALATE_TO_HUMAN,
    STOP_PURSUIT: ActionType.STOP_PURSUIT,
}

EMAIL_TEMPLATE_ID = "email_template_01"


def validate_amount_integrity(candidate_amount_minor: int, case: RecoveryCase) -> None:
    """§8.1 "Amount integrity": a candidate's amount must equal the
    case's authorized recoverable amount exactly. Raises AmountIntegrityError
    on any deviation -- this must never be silently corrected."""
    if candidate_amount_minor != case.principal_amount_minor:
        raise AmountIntegrityError(candidate_amount_minor, case.principal_amount_minor)


def _fixed_parameters(candidate_key: str, case: RecoveryCase) -> dict:
    amount_minor = case.principal_amount_minor
    validate_amount_integrity(amount_minor, case)

    if candidate_key == RETRY_24H:
        return {"delay_hours": 24, "amount_minor": amount_minor}
    if candidate_key == RETRY_72H:
        return {"delay_hours": 72, "amount_minor": amount_minor}
    if candidate_key == SEND_EMAIL:
        return {"template_id": EMAIL_TEMPLATE_ID, "amount_minor": amount_minor}
    if candidate_key == OFFER_ALT_METHOD:
        return {"amount_minor": amount_minor}
    if candidate_key == ESCALATE:
        return {"amount_minor": amount_minor}
    if candidate_key == STOP_PURSUIT:
        return {"amount_minor": amount_minor}
    raise ValueError(f"unrecognized candidate_key: {candidate_key!r}")  # pragma: no cover


def generate_candidates(
    case: RecoveryCase,
    *,
    root_cause: DeclineCode,
    config: PolicyConfig,
    tables: EconomicTables,
    now: datetime,
) -> list[ActionCandidate]:
    """Deterministically build all six fixed candidates for this case.

    Every candidate is returned, including ineligible ones -- callers
    that need only the eligible subset should filter on
    `candidate.eligibility.allowed`. Returning the full set unfiltered
    is what AT-09 #6 means by "a CANDIDATES_GENERATED audit event
    listing all candidate IDs": ineligible candidates are still
    produced and recorded, just never selectable.
    """
    candidates: list[ActionCandidate] = []
    for candidate_key in ALL_CANDIDATE_KEYS:
        parameters = _fixed_parameters(candidate_key, case)
        eligibility = check_candidate_eligibility(candidate_key, case, config, root_cause, now)
        economics = compute_economics(
            candidate_key,
            recoverable_amount_minor=case.principal_amount_minor,
            root_cause=root_cause,
            risk_tier=case.risk_tier,
            tables=tables,
        )
        candidates.append(
            ActionCandidate(
                candidate_id=candidate_key,
                case_id=case.case_id,
                action_type=CANDIDATE_ACTION_TYPES[candidate_key],
                parameters=parameters,
                eligibility=eligibility,
                economics=economics,
            )
        )
    return candidates


def requires_human_approval(case: RecoveryCase, config: PolicyConfig) -> bool:
    """§8.1 human approval threshold: a high-value action must be
    reviewed by a human before it may execute. This never blocks
    eligibility -- it flags the case for mandatory human sign-off
    downstream, which is out of scope for this engine to perform."""
    return case.principal_amount_minor >= config.high_value_approval_threshold_minor
