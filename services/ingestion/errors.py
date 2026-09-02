"""Errors raised by the ingestion layer.

These are distinct from domain.errors: domain errors are about invalid
*state transitions* on an already-constructed case; these are about
rejecting bad input before a case is ever touched.
"""

from __future__ import annotations


class IngestionError(Exception):
    """Base class for ingestion-layer errors."""


class SchemaValidationError(IngestionError):
    """Raised when an event payload fails canonical-schema validation."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(f"Schema validation failed: {'; '.join(errors)}")


class UnknownEventTypeError(IngestionError):
    """Raised when event_type is not one this MVP ingestion service handles.

    Per docs/acceptance-tests.md AT-03c: out-of-MVP-scope event types
    (e.g. "checkout.abandoned") must be rejected, not silently accepted.
    """

    def __init__(self, event_type: str):
        self.event_type = event_type
        super().__init__(f"Unknown or out-of-scope event_type: {event_type!r}")


class SignatureVerificationError(IngestionError):
    """Raised when the event's signature/credential cannot be verified."""

    def __init__(self, source: str):
        self.source = source
        super().__init__(f"Signature verification failed for source: {source!r}")


class DeadLetterNotFoundError(IngestionError):
    """Raised by replay when the given dead-letter id does not exist."""

    def __init__(self, dead_letter_id: str):
        self.dead_letter_id = dead_letter_id
        super().__init__(f"No dead-lettered event found with id: {dead_letter_id!r}")
