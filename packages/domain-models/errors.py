"""Domain-level exceptions."""

from __future__ import annotations


class DomainError(Exception):
    """Base class for all domain-layer errors."""


class InvalidStateTransition(DomainError):
    """Raised when a case attempts a transition that is not permitted.

    Per docs/revised-architecture.md §5:
    "Invalid transitions must be rejected. For example, a case in
    RECOVERED cannot transition back to ACTION_SCHEDULED, and a case in
    CUSTOMER_DISPUTED cannot execute a new recovery action."
    """

    def __init__(self, from_state, to_state, case_id: str | None = None):
        self.from_state = from_state
        self.to_state = to_state
        self.case_id = case_id
        subject = f" for case {case_id}" if case_id else ""
        super().__init__(
            f"Invalid state transition{subject}: {from_state} -> {to_state} "
            "is not permitted by the recovery-case state machine."
        )


class ValidationError(DomainError):
    """Raised when a domain model is constructed with invalid data."""
