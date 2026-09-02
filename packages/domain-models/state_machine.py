"""The explicit recovery-case state machine.

Source of truth: docs/revised-architecture.md §5, cross-checked against
the case-by-case walkthroughs in docs/demo-script.md and the observable
behaviors required by docs/acceptance-tests.md (AT-01, AT-05 through
AT-14). Where a document implies a transition that isn't in the literal
§5 arrow-chain, a comment below cites the source.

Design choice: rather than special-casing "eligibility stop" logic
inline, this module exposes exactly one function, `transition`, that
checks a static adjacency table (ALLOWED_TRANSITIONS). This keeps the
question "is X -> Y ever legal?" answerable by reading one dict, and
keeps forbidden transitions trivially testable (see tests/test_state_machine.py).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from .enums import ActorType, CaseState
from .errors import InvalidStateTransition
from .models import AuditEvent, RecoveryCase

CS = CaseState

# ---------------------------------------------------------------------------
# Adjacency table: from_state -> set of legal to_states.
# ---------------------------------------------------------------------------
ALLOWED_TRANSITIONS: dict[CaseState, frozenset[CaseState]] = {
    # RECEIVED -> NORMALIZED is the only legal next step (AT-01 #3, #4).
    CS.RECEIVED: frozenset({CS.NORMALIZED}),
    # From NORMALIZED, eligibility checking begins. Opt-out and dispute are
    # checked first and can short-circuit straight to their terminal states
    # without ever reaching ELIGIBILITY_CHECKED (docs/demo-script.md Case 5
    # and Case 6 both show "NORMALIZED -> CUSTOMER_OPTED_OUT" / "NORMALIZED
    # -> CUSTOMER_DISPUTED" directly). A case can also expire while queued.
    CS.NORMALIZED: frozenset(
        {
            CS.ELIGIBILITY_CHECKED,
            CS.CUSTOMER_OPTED_OUT,
            CS.CUSTOMER_DISPUTED,
            CS.STOPPED_BY_POLICY,
            CS.EXPIRED,
        }
    ),
    # Once full eligibility passes, diagnosis begins. Cap/cooldown-style
    # stops (docs/demo-script.md Case 4: "ELIGIBILITY_CHECKED ->
    # STOPPED_BY_POLICY", reason CONTACT_CAP_REACHED) as well as late-
    # discovered opt-out/dispute are also legal from here.
    CS.ELIGIBILITY_CHECKED: frozenset(
        {
            CS.DIAGNOSED,
            CS.STOPPED_BY_POLICY,
            CS.CUSTOMER_OPTED_OUT,
            CS.CUSTOMER_DISPUTED,
            CS.EXPIRED,
        }
    ),
    CS.DIAGNOSED: frozenset({CS.CANDIDATES_GENERATED, CS.EXPIRED}),
    # AT-11: "STOPPED_BY_POLICY if no non-contact candidates are eligible."
    CS.CANDIDATES_GENERATED: frozenset(
        {CS.ACTION_SCORED, CS.STOPPED_BY_POLICY, CS.EXPIRED}
    ),
    # AT-11: "STOPPED_BY_POLICY with reason code BELOW_ECONOMIC_THRESHOLD"
    # when no candidate clears the minimum expected-value bar; otherwise
    # proceed to the bounded LLM decision step.
    CS.ACTION_SCORED: frozenset(
        {CS.DECISION_PENDING, CS.STOPPED_BY_POLICY, CS.EXPIRED}
    ),
    # AT-12: an invalid/malformed LLM response is rejected and the system
    # falls back to either the deterministic best candidate (-> VALIDATED,
    # same as a normal valid decision) or human review (-> ESCALATED_TO_HUMAN),
    # per the configurable `llm_fallback_strategy` in policy-decisions.md §15.
    CS.DECISION_PENDING: frozenset(
        {CS.VALIDATED, CS.ESCALATED_TO_HUMAN, CS.EXPIRED}
    ),
    # docs/demo-script.md Case 3: a validated "escalate_to_human" candidate
    # goes straight to ESCALATED_TO_HUMAN instead of being scheduled/executed.
    CS.VALIDATED: frozenset(
        {CS.ACTION_SCHEDULED, CS.ESCALATED_TO_HUMAN, CS.EXPIRED}
    ),
    # AT-14b: an out-of-order success webhook is authoritative even while a
    # scheduled action hasn't executed yet -> cancel it and reconcile as
    # RECOVERED directly from ACTION_SCHEDULED.
    CS.ACTION_SCHEDULED: frozenset(
        {CS.ACTION_EXECUTED, CS.RECOVERED, CS.FAILED_EXECUTION, CS.EXPIRED}
    ),
    CS.ACTION_EXECUTED: frozenset({CS.AWAITING_OUTCOME, CS.FAILED_EXECUTION}),
    # AT-14a (success), AT-14c (failure).
    CS.AWAITING_OUTCOME: frozenset(
        {CS.RECOVERED, CS.FAILED_EXECUTION, CS.EXPIRED}
    ),
    # docs/demo-script.md Case 9: a holdout case stopped by policy (no
    # outreach sent) can still be reconciled to RECOVERED when the customer
    # pays on their own. This is the only transition permitted out of
    # STOPPED_BY_POLICY -- it can never move to an active/in-flight state.
    CS.STOPPED_BY_POLICY: frozenset({CS.RECOVERED}),
    # AT-14c: a failed execution "returns to a state where re-diagnosis is
    # possible (depending on policy)" -- modeled as FAILED_EXECUTION ->
    # DIAGNOSED, so a fresh candidate-generation/scoring/decision cycle can
    # run. No other active state is reachable from here.
    CS.FAILED_EXECUTION: frozenset({CS.DIAGNOSED}),
    # --- Fully terminal states: no automated transition ever leaves them. ---
    # AT-14d: RECOVERED explicitly rejects further transitions.
    CS.RECOVERED: frozenset(),
    CS.ESCALATED_TO_HUMAN: frozenset(),
    CS.CUSTOMER_OPTED_OUT: frozenset(),
    CS.CUSTOMER_DISPUTED: frozenset(),
    CS.EXPIRED: frozenset(),
}

# Sanity check at import time: every CaseState must have an (possibly
# empty) entry in the table, so the adjacency table can never silently
# "forget" a state.
_missing = set(CaseState) - set(ALLOWED_TRANSITIONS)
if _missing:  # pragma: no cover - defensive, should be unreachable
    raise AssertionError(f"ALLOWED_TRANSITIONS is missing states: {_missing}")


def is_transition_allowed(from_state: CaseState, to_state: CaseState) -> bool:
    """Pure predicate -- does not raise, does not mutate anything."""
    return to_state in ALLOWED_TRANSITIONS.get(from_state, frozenset())


def transition(
    case: RecoveryCase,
    to_state: CaseState,
    *,
    actor_type: ActorType,
    actor_version: str,
    reason_codes: tuple[str, ...] = (),
    payload: dict[str, Any] | None = None,
    policy_version: str | None = None,
    correlation_id: str | None = None,
    audit_id: str | None = None,
    now: datetime | None = None,
) -> tuple[RecoveryCase, AuditEvent]:
    """Attempt to move `case` from its current state to `to_state`.

    Returns a *new* RecoveryCase (cases are otherwise mutable, but the
    state field is only ever changed through this function) plus the
    AuditEvent that must be appended to the append-only audit log.

    Raises InvalidStateTransition if the transition is not permitted --
    per docs/revised-architecture.md §5, invalid transitions must be
    rejected, not silently ignored or coerced.
    """
    from_state = case.state
    if not is_transition_allowed(from_state, to_state):
        raise InvalidStateTransition(from_state, to_state, case_id=case.case_id)

    ts = now or datetime.now(timezone.utc)

    new_case = replace(case, state=to_state, updated_at=ts)

    audit_event = AuditEvent(
        audit_id=audit_id or f"audit_{case.case_id}_{from_state}_{to_state}_{ts.timestamp()}",
        case_id=case.case_id,
        event_type="STATE_TRANSITION",
        actor_type=actor_type,
        actor_version=actor_version,
        payload=payload or {},
        created_at=ts,
        previous_state=from_state,
        new_state=to_state,
        reason_codes=reason_codes,
        policy_version=policy_version or case.policy_version,
        correlation_id=correlation_id,
    )

    return new_case, audit_event


def assert_valid_transition(from_state: CaseState, to_state: CaseState) -> None:
    """Raise InvalidStateTransition without needing a full RecoveryCase.

    Useful for exhaustive table-driven tests over all (from, to) pairs.
    """
    if not is_transition_allowed(from_state, to_state):
        raise InvalidStateTransition(from_state, to_state)
