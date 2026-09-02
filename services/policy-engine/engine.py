"""The Recovery Policy and Economic Engine orchestrator.

Wires eligibility.py, candidates.py, and scoring.py into the actual
case-state transitions and audit events docs/acceptance-tests.md
AT-04 through AT-10 require. Uses the existing, frozen
domain.state_machine.transition() for every state change -- this
module adds no new edges to that adjacency table, it only calls into
transitions that already exist (NORMALIZED -> ELIGIBILITY_CHECKED /
CUSTOMER_OPTED_OUT / CUSTOMER_DISPUTED / STOPPED_BY_POLICY; DIAGNOSED
-> CANDIDATES_GENERATED -> ACTION_SCORED -> DECISION_PENDING, with
STOPPED_BY_POLICY branches at each of the last two).

No LLM, no real payment or messaging integration lives here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from domain.enums import ActorType, CaseState, DeclineCode
from domain.models import ActionCandidate, AuditEvent, RecoveryCase
from domain.state_machine import transition as case_transition
from ingestion.stores import AuditLog, CaseRepository

from .candidates import generate_candidates, requires_human_approval
from .config import NON_CONTACT_CANDIDATE_KEYS, EconomicTables, PolicyConfig
from .eligibility import check_case_level_stop
from .scoring import EngineOutcome, clears_economic_threshold

ENGINE_ACTOR_VERSION = "policy-engine-v1"

EligibilityStatus = Literal["PASSED", "BLOCKED"]


@dataclass
class EligibilityRunResult:
    status: EligibilityStatus
    case: RecoveryCase
    reason_code: str | None
    audit_events: list[AuditEvent] = field(default_factory=list)


@dataclass
class EngineRunResult:
    outcome: EngineOutcome
    case: RecoveryCase
    candidates: list[ActionCandidate]
    requires_human_approval: bool
    audit_events: list[AuditEvent] = field(default_factory=list)


class RecoveryPolicyEngine:
    def __init__(
        self,
        config: PolicyConfig,
        tables: EconomicTables,
        cases: CaseRepository,
        audit_log: AuditLog,
    ):
        self.config = config
        self.tables = tables
        self.cases = cases
        self.audit_log = audit_log

    # -- §8.1 eligibility -------------------------------------------------

    def run_eligibility(self, case: RecoveryCase, *, now: datetime | None = None) -> EligibilityRunResult:
        """Runs the absolute case-level stop checks (opt-out, dispute,
        legal/account hold, canceled/already-paid subscription).

        Expects `case` in NORMALIZED. On a stop, transitions straight to
        the terminal/stopped state and appends ELIGIBILITY_BLOCKED
        (AT-04 #5, AT-05 #5). Otherwise transitions to ELIGIBILITY_CHECKED.
        """
        now = now or datetime.now(timezone.utc)
        stop = check_case_level_stop(case, self.config)

        if stop is not None:
            new_case, _ = case_transition(
                case,
                stop.target_state,
                actor_type=ActorType.ELIGIBILITY_ENGINE,
                actor_version=ENGINE_ACTOR_VERSION,
                reason_codes=(stop.reason_code,),
                now=now,
            )
            self.cases.save(new_case)
            audit = self._append_audit(
                case_id=case.case_id,
                event_type="ELIGIBILITY_BLOCKED",
                actor_type=ActorType.ELIGIBILITY_ENGINE,
                payload={"reason_code": stop.reason_code},
                now=now,
                previous_state=case.state,
                new_state=stop.target_state,
                reason_codes=(stop.reason_code,),
            )
            return EligibilityRunResult(
                status="BLOCKED", case=new_case, reason_code=stop.reason_code, audit_events=[audit]
            )

        new_case, _ = case_transition(
            case,
            CaseState.ELIGIBILITY_CHECKED,
            actor_type=ActorType.ELIGIBILITY_ENGINE,
            actor_version=ENGINE_ACTOR_VERSION,
            now=now,
        )
        self.cases.save(new_case)
        audit = self._append_audit(
            case_id=case.case_id,
            event_type="ELIGIBILITY_CHECKED",
            actor_type=ActorType.ELIGIBILITY_ENGINE,
            payload={},
            now=now,
            previous_state=case.state,
            new_state=CaseState.ELIGIBILITY_CHECKED,
        )
        return EligibilityRunResult(status="PASSED", case=new_case, reason_code=None, audit_events=[audit])

    # -- §8.2 candidate generation + §8.3 expected-value scoring ---------

    def generate_and_score(
        self,
        case: RecoveryCase,
        *,
        root_cause: DeclineCode,
        now: datetime | None = None,
    ) -> EngineRunResult:
        """Expects `case` in DIAGNOSED (AT-09 precondition). Generates
        all six fixed candidates, scores them, and drives the case
        through CANDIDATES_GENERATED -> ACTION_SCORED -> DECISION_PENDING,
        or into STOPPED_BY_POLICY at either step per AT-07 #2 / AT-10.
        """
        now = now or datetime.now(timezone.utc)
        audit_events: list[AuditEvent] = []

        candidates = generate_candidates(
            case, root_cause=root_cause, config=self.config, tables=self.tables, now=now
        )

        case_generated, _ = case_transition(
            case,
            CaseState.CANDIDATES_GENERATED,
            actor_type=ActorType.POLICY_ENGINE,
            actor_version=ENGINE_ACTOR_VERSION,
            now=now,
        )
        self.cases.save(case_generated)
        audit_events.append(
            self._append_audit(
                case_id=case.case_id,
                event_type="CANDIDATES_GENERATED",
                actor_type=ActorType.POLICY_ENGINE,
                payload={"candidate_ids": [c.candidate_id for c in candidates]},
                now=now,
                previous_state=case.state,
                new_state=CaseState.CANDIDATES_GENERATED,
            )
        )

        eligible = [c for c in candidates if c.eligibility.allowed]
        eligible_non_contact = [c for c in eligible if c.candidate_id in NON_CONTACT_CANDIDATE_KEYS]

        if not eligible_non_contact:
            # AT-07 #2: nothing actionable remains besides the null
            # stop_pursuit candidate -- stop here rather than proceed to
            # scoring/decision.
            stopped_case, _ = case_transition(
                case_generated,
                CaseState.STOPPED_BY_POLICY,
                actor_type=ActorType.POLICY_ENGINE,
                actor_version=ENGINE_ACTOR_VERSION,
                reason_codes=("NO_ACTIONABLE_CANDIDATES",),
                now=now,
            )
            self.cases.save(stopped_case)
            audit_events.append(
                self._append_audit(
                    case_id=case.case_id,
                    event_type="CASE_STOPPED",
                    actor_type=ActorType.POLICY_ENGINE,
                    payload={"candidate_ids": [c.candidate_id for c in candidates]},
                    now=now,
                    previous_state=CaseState.CANDIDATES_GENERATED,
                    new_state=CaseState.STOPPED_BY_POLICY,
                    reason_codes=("NO_ACTIONABLE_CANDIDATES",),
                )
            )
            return EngineRunResult(
                outcome="BLOCKED_BY_POLICY",
                case=stopped_case,
                candidates=candidates,
                requires_human_approval=requires_human_approval(case, self.config),
                audit_events=audit_events,
            )

        scored_case, _ = case_transition(
            case_generated,
            CaseState.ACTION_SCORED,
            actor_type=ActorType.POLICY_ENGINE,
            actor_version=ENGINE_ACTOR_VERSION,
            now=now,
        )
        self.cases.save(scored_case)
        audit_events.append(
            self._append_audit(
                case_id=case.case_id,
                event_type="CANDIDATES_SCORED",
                actor_type=ActorType.POLICY_ENGINE,
                payload={
                    "scores": {
                        c.candidate_id: c.economics.expected_net_recovery_minor for c in candidates
                    }
                },
                now=now,
                previous_state=CaseState.CANDIDATES_GENERATED,
                new_state=CaseState.ACTION_SCORED,
            )
        )

        any_above_threshold = any(
            clears_economic_threshold(c, self.config.minimum_expected_net_recovery_minor)
            for c in eligible
        )

        if not any_above_threshold:
            stopped_case, _ = case_transition(
                scored_case,
                CaseState.STOPPED_BY_POLICY,
                actor_type=ActorType.POLICY_ENGINE,
                actor_version=ENGINE_ACTOR_VERSION,
                reason_codes=("BELOW_ECONOMIC_THRESHOLD",),
                now=now,
            )
            self.cases.save(stopped_case)
            audit_events.append(
                self._append_audit(
                    case_id=case.case_id,
                    event_type="CASE_STOPPED",
                    actor_type=ActorType.POLICY_ENGINE,
                    payload={"reason": "BELOW_ECONOMIC_THRESHOLD"},
                    now=now,
                    previous_state=CaseState.ACTION_SCORED,
                    new_state=CaseState.STOPPED_BY_POLICY,
                    reason_codes=("BELOW_ECONOMIC_THRESHOLD",),
                )
            )
            return EngineRunResult(
                outcome="NO_ACTION_ABOVE_THRESHOLD",
                case=stopped_case,
                candidates=candidates,
                requires_human_approval=requires_human_approval(case, self.config),
                audit_events=audit_events,
            )

        pending_case, _ = case_transition(
            scored_case,
            CaseState.DECISION_PENDING,
            actor_type=ActorType.POLICY_ENGINE,
            actor_version=ENGINE_ACTOR_VERSION,
            now=now,
        )
        self.cases.save(pending_case)

        return EngineRunResult(
            outcome="ACTIONABLE_CANDIDATES",
            case=pending_case,
            candidates=candidates,
            requires_human_approval=requires_human_approval(case, self.config),
            audit_events=audit_events,
        )

    # -- internals ------------------------------------------------------

    def _append_audit(
        self,
        *,
        case_id: str,
        event_type: str,
        actor_type: ActorType,
        payload: dict,
        now: datetime,
        previous_state: CaseState | None = None,
        new_state: CaseState | None = None,
        reason_codes: tuple[str, ...] = (),
    ) -> AuditEvent:
        event = AuditEvent(
            audit_id=f"audit_{uuid.uuid4().hex[:12]}",
            case_id=case_id,
            event_type=event_type,
            actor_type=actor_type,
            actor_version=ENGINE_ACTOR_VERSION,
            payload=payload,
            created_at=now,
            previous_state=previous_state,
            new_state=new_state,
            reason_codes=reason_codes,
        )
        self.audit_log.append(event)
        return event
