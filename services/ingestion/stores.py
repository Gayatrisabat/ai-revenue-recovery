"""In-memory persistence for the ingestion layer.

These stand in for the real tables named in
docs/revised-architecture.md §13 (`raw_events`) and the dead-letter
table required by §6 / docs/acceptance-tests.md AT-03. They are plain
Python objects on purpose -- no database driver, no network -- so the
ingestion service and its tests can run anywhere. Swapping in a real
database later means writing classes with the same method signatures.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from domain.enums import TERMINAL_STATES
from domain.models import AuditEvent, RecoveryCase


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# raw_events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawEventRecord:
    """One persisted, as-received submission.

    Every submission is stored -- including duplicates -- per the task's
    "preserve duplicate, delayed, and out-of-order events for testing"
    requirement and the allowance in AT-02's adversarial variant ("...or
    all 10 with dedup flags; implementation decides").
    """

    raw_event_record_id: str
    event_id: str
    event_type: str
    family: str  # "payment_failure" | "outcome"
    payload: dict[str, Any]
    source: str
    signature: str
    received_at: datetime
    is_duplicate: bool


class RawEventStore:
    """Append-only. Never mutates or removes a record once added."""

    def __init__(self) -> None:
        self._records: list[RawEventRecord] = []
        self._by_event_id: dict[str, list[RawEventRecord]] = {}

    def add(
        self,
        *,
        event_id: str,
        event_type: str,
        family: str,
        payload: dict[str, Any],
        source: str,
        signature: str,
        received_at: datetime,
        is_duplicate: bool,
    ) -> RawEventRecord:
        record = RawEventRecord(
            raw_event_record_id=_new_id("raw"),
            event_id=event_id,
            event_type=event_type,
            family=family,
            payload=payload,
            source=source,
            signature=signature,
            received_at=received_at,
            is_duplicate=is_duplicate,
        )
        self._records.append(record)
        self._by_event_id.setdefault(event_id, []).append(record)
        return record

    def all(self) -> list[RawEventRecord]:
        """Returned in submission (arrival) order -- never reordered."""
        return list(self._records)

    def by_event_id(self, event_id: str) -> list[RawEventRecord]:
        return list(self._by_event_id.get(event_id, []))

    def __len__(self) -> int:
        return len(self._records)


# ---------------------------------------------------------------------------
# deduplication store (idempotency index over event_id)
# ---------------------------------------------------------------------------


class DedupStore:
    """Tracks which event_ids have already been accepted for processing.

    Kept separate from RawEventStore because RawEventStore preserves
    every submission (including duplicates) while this store answers
    exactly one question, cheaply: "have we processed this event_id
    before?"
    """

    def __init__(self) -> None:
        self._first_seen: dict[str, str] = {}  # event_id -> raw_event_record_id
        self._case_id: dict[str, str] = {}  # event_id -> case_id it ended up attached to

    def is_duplicate(self, event_id: str) -> bool:
        return event_id in self._first_seen

    def mark_seen(self, event_id: str, raw_event_record_id: str) -> None:
        self._first_seen.setdefault(event_id, raw_event_record_id)

    def first_seen_record_id(self, event_id: str) -> str | None:
        return self._first_seen.get(event_id)

    def set_case_id(self, event_id: str, case_id: str) -> None:
        self._case_id[event_id] = case_id

    def case_id_for(self, event_id: str) -> str | None:
        return self._case_id.get(event_id)

    def __len__(self) -> int:
        return len(self._first_seen)


# ---------------------------------------------------------------------------
# dead-letter / quarantine table
# ---------------------------------------------------------------------------


@dataclass
class DeadLetterRecord:
    dead_letter_id: str
    payload: dict[str, Any]
    event_id: str | None
    errors: list[str]
    reason: str  # "schema_invalid" | "unknown_event_type" | "signature_invalid"
    quarantined_at: datetime
    source: str
    signature: str
    replay_count: int = 0
    resolved: bool = False
    resolved_raw_event_record_id: str | None = None


class DeadLetterStore:
    def __init__(self) -> None:
        self._records: dict[str, DeadLetterRecord] = {}

    def add(
        self,
        *,
        payload: dict[str, Any],
        event_id: str | None,
        errors: list[str],
        reason: str,
        quarantined_at: datetime,
        source: str,
        signature: str,
    ) -> DeadLetterRecord:
        record = DeadLetterRecord(
            dead_letter_id=_new_id("dlq"),
            payload=payload,
            event_id=event_id,
            errors=list(errors),
            reason=reason,
            quarantined_at=quarantined_at,
            source=source,
            signature=signature,
        )
        self._records[record.dead_letter_id] = record
        return record

    def get(self, dead_letter_id: str) -> DeadLetterRecord | None:
        return self._records.get(dead_letter_id)

    def all(self) -> list[DeadLetterRecord]:
        return list(self._records.values())

    def unresolved(self) -> list[DeadLetterRecord]:
        return [r for r in self._records.values() if not r.resolved]

    def mark_replayed(self, dead_letter_id: str, *, resolved: bool, raw_event_record_id: str | None) -> None:
        record = self._records[dead_letter_id]
        record.replay_count += 1
        if resolved:
            record.resolved = True
            record.resolved_raw_event_record_id = raw_event_record_id

    def __len__(self) -> int:
        return len(self._records)


# ---------------------------------------------------------------------------
# recovery_cases
# ---------------------------------------------------------------------------


class CaseRepository:
    def __init__(self) -> None:
        self._cases: dict[str, RecoveryCase] = {}

    def save(self, case: RecoveryCase) -> None:
        self._cases[case.case_id] = case

    def get(self, case_id: str) -> RecoveryCase | None:
        return self._cases.get(case_id)

    def find_open_case(self, customer_id: str, subscription_id: str) -> RecoveryCase | None:
        """Most recently created non-terminal case for this episode key.

        "Open" mirrors domain.enums.TERMINAL_STATES: RECOVERED,
        ESCALATED_TO_HUMAN, CUSTOMER_OPTED_OUT, CUSTOMER_DISPUTED, and
        EXPIRED are closed episodes -- a new payment failure for the same
        customer/subscription after one of those starts a new episode
        (new case_id) rather than reopening the old one.
        """
        candidates = [
            case
            for case in self._cases.values()
            if case.customer_id == customer_id
            and case.subscription_id == subscription_id
            and case.state not in TERMINAL_STATES
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda c: c.created_at)

    def all(self) -> list[RecoveryCase]:
        return list(self._cases.values())

    def __len__(self) -> int:
        return len(self._cases)


# ---------------------------------------------------------------------------
# audit_events
# ---------------------------------------------------------------------------


class AuditLog:
    """Append-only. Mirrors docs/revised-architecture.md §12."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> None:
        self._events.append(event)

    def all(self) -> list[AuditEvent]:
        return list(self._events)

    def for_case(self, case_id: str) -> list[AuditEvent]:
        return [e for e in self._events if e.case_id == case_id]

    def __len__(self) -> int:
        return len(self._events)


@dataclass
class IngestionDatabase:
    """Convenience bundle of every store the ingestion service touches."""

    raw_events: RawEventStore = field(default_factory=RawEventStore)
    dedup: DedupStore = field(default_factory=DedupStore)
    dead_letters: DeadLetterStore = field(default_factory=DeadLetterStore)
    cases: CaseRepository = field(default_factory=CaseRepository)
    audit_log: AuditLog = field(default_factory=AuditLog)
