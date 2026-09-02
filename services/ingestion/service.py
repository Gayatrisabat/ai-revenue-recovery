"""The ingestion service.

Orchestrates, in order, exactly the steps from
docs/revised-architecture.md §6:

  1. verify the gateway signature or trusted source credential;
  2. validate the event against the canonical schema;
  3. use event_id as an idempotency key;
  4. persist the raw event before downstream processing;
  5. create or update the corresponding recovery case;
  6. reject or quarantine malformed events without executing an action.

Stops at NORMALIZED. Never runs eligibility, diagnosis, candidate
generation, LLM selection, or execution -- and never contacts a real
gateway, messaging provider, or LLM.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from domain.enums import ActorType, CaseState, Currency
from domain.errors import InvalidStateTransition
from domain.models import AuditEvent, RecoveryCase
from domain.state_machine import transition as case_transition

from .errors import DeadLetterNotFoundError
from .schema import classify_event_type, validate_outcome_event, validate_payment_failure_event
from .signature import SignatureVerifier, canonical_bytes
from .stores import IngestionDatabase

IngestStatus = Literal["ingested", "duplicate", "dead_lettered"]

INGESTION_ACTOR_VERSION = "ingestion-service-v1"


@dataclass
class IngestResult:
    status: IngestStatus
    event_id: str | None
    family: str | None  # "payment_failure" | "outcome" | None (unknown/rejected)
    raw_event_record_id: str | None = None
    case_id: str | None = None
    dead_letter_id: str | None = None
    dead_letter_reason: str | None = None
    errors: list[str] = field(default_factory=list)
    audit_events: list[AuditEvent] = field(default_factory=list)
    case_matched: bool = False  # relevant for outcome events


class IngestionService:
    def __init__(self, db: IngestionDatabase, signature_verifier: SignatureVerifier):
        self.db = db
        self.signature_verifier = signature_verifier

    # -- public API ---------------------------------------------------

    def ingest_event(
        self,
        payload: dict[str, Any],
        *,
        signature: str,
        source: str,
        now: datetime | None = None,
    ) -> IngestResult:
        now = now or datetime.now(timezone.utc)
        event_id = self._extract_event_id(payload)
        event_type = payload.get("event_type") if isinstance(payload, dict) else None

        # Step 1: signature verification. A bad signature is treated the
        # same as any other untrusted/malformed input: quarantine, never
        # touch a case.
        if not self._verify_signature(payload, signature, source):
            return self._quarantine(
                payload,
                event_id=event_id,
                reason="signature_invalid",
                errors=[f"signature verification failed for source={source!r}"],
                source=source,
                signature=signature,
                now=now,
            )

        # Step 2 (part 1): is this even a family of event we handle?
        family = classify_event_type(event_type)
        if family == "unknown":
            return self._quarantine(
                payload,
                event_id=event_id,
                reason="unknown_event_type",
                errors=[f"event_type {event_type!r} is out of MVP scope"],
                source=source,
                signature=signature,
                now=now,
            )

        # Step 2 (part 2): canonical schema validation.
        errors = (
            validate_payment_failure_event(payload)
            if family == "payment_failure"
            else validate_outcome_event(payload)
        )
        if errors:
            return self._quarantine(
                payload,
                event_id=event_id,
                reason="schema_invalid",
                errors=errors,
                source=source,
                signature=signature,
                now=now,
            )

        # Step 3 + 4: idempotency check, then persist the raw event
        # regardless of duplicate status (duplicates are preserved for
        # testing/audit, not dropped).
        is_dup = self.db.dedup.is_duplicate(event_id)
        raw_record = self.db.raw_events.add(
            event_id=event_id,
            event_type=event_type,
            family=family,
            payload=payload,
            source=source,
            signature=signature,
            received_at=now,
            is_duplicate=is_dup,
        )

        if is_dup:
            return self._handle_duplicate(event_id, raw_record.raw_event_record_id, now)

        self.db.dedup.mark_seen(event_id, raw_record.raw_event_record_id)

        # Step 5: create or update the case (payment-failure events only;
        # outcome events are matched to a case for later reconciliation by
        # a downstream layer, but never mutate case state here).
        if family == "payment_failure":
            case, audit_events = self._create_or_update_case(payload, event_id, now)
            return IngestResult(
                status="ingested",
                event_id=event_id,
                family=family,
                raw_event_record_id=raw_record.raw_event_record_id,
                case_id=case.case_id,
                audit_events=audit_events,
            )

        # family == "outcome"
        case, audit_events = self._attach_outcome_to_case(payload, event_id, now)
        return IngestResult(
            status="ingested",
            event_id=event_id,
            family=family,
            raw_event_record_id=raw_record.raw_event_record_id,
            case_id=case.case_id if case else None,
            case_matched=case is not None,
            audit_events=audit_events,
        )

    def replay_dead_letter(
        self,
        dead_letter_id: str,
        *,
        payload_override: dict[str, Any] | None = None,
        signature_override: str | None = None,
        source_override: str | None = None,
        now: datetime | None = None,
    ) -> IngestResult:
        """Re-attempt ingestion of a quarantined event.

        Per the build runbook's replay requirement and
        docs/revised-architecture.md §6 ("a replay command for failed
        events"). Overrides let a human operator correct e.g. a missing
        field or an out-of-date signature before replaying -- the
        original quarantined payload is never mutated in place.
        """
        record = self.db.dead_letters.get(dead_letter_id)
        if record is None:
            raise DeadLetterNotFoundError(dead_letter_id)

        result = self.ingest_event(
            payload_override if payload_override is not None else dict(record.payload),
            signature=signature_override if signature_override is not None else record.signature,
            source=source_override if source_override is not None else record.source,
            now=now,
        )

        resolved = result.status in ("ingested", "duplicate")
        self.db.dead_letters.mark_replayed(
            dead_letter_id,
            resolved=resolved,
            raw_event_record_id=result.raw_event_record_id,
        )
        return result

    # -- internals ------------------------------------------------------

    @staticmethod
    def _extract_event_id(payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None
        value = payload.get("event_id")
        return value if isinstance(value, str) and value.strip() else None

    def _verify_signature(self, payload: Any, signature: str, source: str) -> bool:
        if not isinstance(payload, dict):
            return False
        try:
            return self.signature_verifier.verify(canonical_bytes(payload), signature, source)
        except Exception:
            return False

    def _quarantine(
        self,
        payload: Any,
        *,
        event_id: str | None,
        reason: str,
        errors: list[str],
        source: str,
        signature: str,
        now: datetime,
    ) -> IngestResult:
        safe_payload = payload if isinstance(payload, dict) else {"_non_dict_payload": repr(payload)}
        record = self.db.dead_letters.add(
            payload=safe_payload,
            event_id=event_id,
            errors=errors,
            reason=reason,
            quarantined_at=now,
            source=source,
            signature=signature,
        )
        family = classify_event_type(payload.get("event_type")) if isinstance(payload, dict) else "unknown"
        return IngestResult(
            status="dead_lettered",
            event_id=event_id,
            family=None if family == "unknown" else family,
            dead_letter_id=record.dead_letter_id,
            dead_letter_reason=reason,
            errors=errors,
        )

    def _handle_duplicate(
        self, event_id: str, raw_event_record_id: str, now: datetime
    ) -> IngestResult:
        case_id = self.db.dedup.case_id_for(event_id)
        audit_events: list[AuditEvent] = []
        if case_id is not None:
            audit = self._append_audit(
                case_id=case_id,
                event_type="EVENT_DEDUPLICATED",
                actor_type=ActorType.INGESTION_SERVICE,
                payload={"duplicate_event_id": event_id},
                now=now,
                reason_codes=("DUPLICATE_EVENT_ID",),
                correlation_id=event_id,
            )
            audit_events.append(audit)
        return IngestResult(
            status="duplicate",
            event_id=event_id,
            family=None,
            raw_event_record_id=raw_event_record_id,
            case_id=case_id,
            audit_events=audit_events,
        )

    def _create_or_update_case(
        self, payload: dict[str, Any], event_id: str, now: datetime
    ) -> tuple[RecoveryCase, list[AuditEvent]]:
        customer_id = payload["customer_id"]
        subscription_id = payload["subscription_id"]
        amount_minor = payload["amount_minor"]
        currency = Currency(payload["currency"])

        existing = self.db.cases.find_open_case(customer_id, subscription_id)
        audit_events: list[AuditEvent] = []

        if existing is None:
            case = RecoveryCase(
                case_id=f"case_{event_id}",
                customer_id=customer_id,
                subscription_id=subscription_id,
                principal_amount_minor=amount_minor,
                currency=currency,
                state=CaseState.RECEIVED,
                created_at=now,
                updated_at=now,
            )
            self.db.cases.save(case)
            audit_events.append(
                self._append_audit(
                    case_id=case.case_id,
                    event_type="EVENT_RECEIVED",
                    actor_type=ActorType.INGESTION_SERVICE,
                    payload={"event_id": event_id, "raw_event_type": payload.get("event_type")},
                    now=now,
                    previous_state=None,
                    new_state=CaseState.RECEIVED,
                    correlation_id=event_id,
                )
            )

            normalized_case, _generic_audit = case_transition(
                case,
                CaseState.NORMALIZED,
                actor_type=ActorType.INGESTION_SERVICE,
                actor_version=INGESTION_ACTOR_VERSION,
                now=now,
            )
            self.db.cases.save(normalized_case)
            audit_events.append(
                self._append_audit(
                    case_id=normalized_case.case_id,
                    event_type="EVENT_NORMALIZED",
                    actor_type=ActorType.INGESTION_SERVICE,
                    payload={
                        "event_id": event_id,
                        "amount_minor": amount_minor,
                        "currency": currency.value,
                    },
                    now=now,
                    previous_state=CaseState.RECEIVED,
                    new_state=CaseState.NORMALIZED,
                    correlation_id=event_id,
                )
            )
            self.db.dedup.set_case_id(event_id, normalized_case.case_id)
            return normalized_case, audit_events

        # Existing open episode: attach this additional failure event
        # without forcing any state transition (this layer never executes
        # actions or advances a case past ingestion on its own).
        existing.principal_amount_minor = amount_minor
        existing.retry_count_episode += 1
        existing.updated_at = now
        self.db.cases.save(existing)
        audit_events.append(
            self._append_audit(
                case_id=existing.case_id,
                event_type="CASE_UPDATED",
                actor_type=ActorType.INGESTION_SERVICE,
                payload={
                    "event_id": event_id,
                    "reason": "additional_payment_failure_for_open_episode",
                    "attempt_number": payload.get("attempt_number"),
                },
                now=now,
                previous_state=existing.state,
                new_state=existing.state,
                correlation_id=event_id,
            )
        )
        self.db.dedup.set_case_id(event_id, existing.case_id)
        return existing, audit_events

    def _attach_outcome_to_case(
        self, payload: dict[str, Any], event_id: str, now: datetime
    ) -> tuple[RecoveryCase | None, list[AuditEvent]]:
        customer_id = payload["customer_id"]
        subscription_id = payload["subscription_id"]
        case = self.db.cases.find_open_case(customer_id, subscription_id)
        if case is None:
            return None, []

        audit_event = self._append_audit(
            case_id=case.case_id,
            event_type="OUTCOME_EVENT_RECEIVED",
            actor_type=ActorType.INGESTION_SERVICE,
            payload={
                "event_id": event_id,
                "outcome_event_type": payload.get("event_type"),
                "execution_id": payload.get("execution_id"),
            },
            now=now,
            previous_state=case.state,
            new_state=case.state,
            correlation_id=event_id,
        )
        self.db.dedup.set_case_id(event_id, case.case_id)
        return case, [audit_event]

    def _append_audit(
        self,
        *,
        case_id: str,
        event_type: str,
        actor_type: ActorType,
        payload: dict[str, Any],
        now: datetime,
        previous_state: CaseState | None = None,
        new_state: CaseState | None = None,
        reason_codes: tuple[str, ...] = (),
        correlation_id: str | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            audit_id=f"audit_{uuid.uuid4().hex[:12]}",
            case_id=case_id,
            event_type=event_type,
            actor_type=actor_type,
            actor_version=INGESTION_ACTOR_VERSION,
            payload=payload,
            created_at=now,
            previous_state=previous_state,
            new_state=new_state,
            reason_codes=reason_codes,
            correlation_id=correlation_id,
        )
        self.db.audit_log.append(event)
        return event
