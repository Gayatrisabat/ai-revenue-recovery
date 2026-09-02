"""Canonical event schemas.

Two event families are accepted by this MVP, per the task requirements
and docs/revised-architecture.md §4.1 / §10:

  - PAYMENT_FAILURE_EVENT_TYPES: subscription payment-failure events.
    Fields match §4.1's "Recovery event" JSON example exactly.
  - OUTCOME_EVENT_TYPES: gateway/messaging outcome events (payment
    succeeded/failed, message delivered/failed, customer responded).
    These are accepted, validated, deduplicated, and persisted by this
    layer, but -- per the task's "without executing actions" requirement
    -- are NOT reconciled against a case here. Reconciliation belongs to
    the execution/measurement plane (§10-11), which is out of scope.

Any other event_type (e.g. "checkout.abandoned") is out of MVP scope
and must be rejected (AT-03c).

Validation here is deliberately hand-rolled rather than pulling in a
schema library: the field set is small, stable, and fully enumerated in
the architecture doc, and a dependency-free validator is easier to
audit line-by-line against that doc.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from domain.enums import Currency, DeclineCode

PAYMENT_FAILURE_EVENT_TYPES = frozenset({"subscription.payment_failed"})

OUTCOME_EVENT_TYPES = frozenset(
    {
        "payment.succeeded",
        "payment.failed",
        "message.delivered",
        "message.failed",
        "customer.responded",
    }
)

KNOWN_EVENT_TYPES = PAYMENT_FAILURE_EVENT_TYPES | OUTCOME_EVENT_TYPES

_VALID_CURRENCIES = {c.value for c in Currency}
_VALID_DECLINE_CODES = {c.value for c in DeclineCode}

# Common envelope fields required on every event, regardless of family.
_COMMON_REQUIRED_STRING_FIELDS = (
    "event_id",
    "event_type",
    "source",
    "customer_id",
    "subscription_id",
)

_PAYMENT_FAILURE_REQUIRED_STRING_FIELDS = _COMMON_REQUIRED_STRING_FIELDS + (
    "decline_code",
    "payment_method_type",
    "payment_method_fingerprint",
)


def _is_non_bool_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_iso_timestamp(value: Any, field_name: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not value:
        errors.append(f"{field_name} must be a non-empty ISO 8601 timestamp string")
        return
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{field_name} is not a valid ISO 8601 timestamp: {value!r}")


def _validate_required_strings(
    payload: dict[str, Any], field_names: tuple[str, ...], errors: list[str]
) -> None:
    for field_name in field_names:
        if field_name not in payload:
            errors.append(f"missing required field: {field_name}")
            continue
        value = payload[field_name]
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{field_name} must be a non-empty string, got {value!r}")


def _validate_amount_minor(payload: dict[str, Any], errors: list[str]) -> None:
    if "amount_minor" not in payload:
        errors.append("missing required field: amount_minor")
        return
    value = payload["amount_minor"]
    if value is None:
        errors.append("amount_minor must not be null")
    elif not _is_non_bool_int(value):
        errors.append(f"amount_minor must be an integer, got {type(value).__name__}: {value!r}")
    elif value < 0:
        errors.append(f"amount_minor must be >= 0, got {value}")


def _validate_currency(payload: dict[str, Any], errors: list[str]) -> None:
    if "currency" not in payload:
        errors.append("missing required field: currency")
        return
    value = payload["currency"]
    if value not in _VALID_CURRENCIES:
        errors.append(f"currency must be one of {sorted(_VALID_CURRENCIES)}, got {value!r}")


def validate_payment_failure_event(payload: dict[str, Any]) -> list[str]:
    """Validate a subscription.payment_failed payload against §4.1.

    Returns a list of human-readable error strings; empty means valid.
    """
    errors: list[str] = []

    if not isinstance(payload, dict):
        return [f"payload must be a JSON object, got {type(payload).__name__}"]

    _validate_required_strings(payload, _PAYMENT_FAILURE_REQUIRED_STRING_FIELDS, errors)
    _validate_amount_minor(payload, errors)
    _validate_currency(payload, errors)
    _validate_iso_timestamp(payload.get("occurred_at"), "occurred_at", errors)

    if payload.get("event_type") not in PAYMENT_FAILURE_EVENT_TYPES:
        errors.append(
            f"event_type must be one of {sorted(PAYMENT_FAILURE_EVENT_TYPES)}, "
            f"got {payload.get('event_type')!r}"
        )

    decline_code = payload.get("decline_code")
    if decline_code is not None and decline_code not in _VALID_DECLINE_CODES:
        errors.append(
            f"decline_code must be one of {sorted(_VALID_DECLINE_CODES)}, got {decline_code!r}"
        )

    attempt_number = payload.get("attempt_number")
    if attempt_number is None:
        errors.append("missing required field: attempt_number")
    elif not _is_non_bool_int(attempt_number) or attempt_number < 1:
        errors.append(f"attempt_number must be an integer >= 1, got {attempt_number!r}")

    return errors


def validate_outcome_event(payload: dict[str, Any]) -> list[str]:
    """Validate a payment/messaging outcome payload.

    Required fields: event_id, event_type, occurred_at, source,
    customer_id, subscription_id, amount_minor, currency.
    execution_id is optional (a natural/unassisted payment has none).
    """
    errors: list[str] = []

    if not isinstance(payload, dict):
        return [f"payload must be a JSON object, got {type(payload).__name__}"]

    _validate_required_strings(payload, _COMMON_REQUIRED_STRING_FIELDS, errors)
    _validate_amount_minor(payload, errors)
    _validate_currency(payload, errors)
    _validate_iso_timestamp(payload.get("occurred_at"), "occurred_at", errors)

    if payload.get("event_type") not in OUTCOME_EVENT_TYPES:
        errors.append(
            f"event_type must be one of {sorted(OUTCOME_EVENT_TYPES)}, "
            f"got {payload.get('event_type')!r}"
        )

    execution_id = payload.get("execution_id")
    if execution_id is not None and (
        not isinstance(execution_id, str) or not execution_id.strip()
    ):
        errors.append(f"execution_id, if present, must be a non-empty string, got {execution_id!r}")

    return errors


def classify_event_type(event_type: Any) -> str:
    """Return "payment_failure", "outcome", or "unknown" for routing."""
    if event_type in PAYMENT_FAILURE_EVENT_TYPES:
        return "payment_failure"
    if event_type in OUTCOME_EVENT_TYPES:
        return "outcome"
    return "unknown"
