from __future__ import annotations

import pytest

from ingestion.schema import (
    classify_event_type,
    validate_outcome_event,
    validate_payment_failure_event,
)


def valid_payment_failure_payload(**overrides):
    payload = {
        "event_id": "evt_01",
        "event_type": "subscription.payment_failed",
        "occurred_at": "2026-08-23T10:30:00Z",
        "source": "mock_gateway",
        "customer_id": "cus_123",
        "subscription_id": "sub_456",
        "amount_minor": 149900,
        "currency": "INR",
        "decline_code": "insufficient_funds",
        "payment_method_type": "card",
        "payment_method_fingerprint": "pm_fp_789",
        "attempt_number": 1,
        "metadata": {},
    }
    payload.update(overrides)
    return payload


def valid_outcome_payload(**overrides):
    payload = {
        "event_id": "evt_out_01",
        "event_type": "payment.succeeded",
        "occurred_at": "2026-08-24T09:00:00Z",
        "source": "mock_gateway",
        "customer_id": "cus_123",
        "subscription_id": "sub_456",
        "amount_minor": 149900,
        "currency": "INR",
        "execution_id": "exec_001",
    }
    payload.update(overrides)
    return payload


class TestClassifyEventType:
    def test_payment_failure(self):
        assert classify_event_type("subscription.payment_failed") == "payment_failure"

    @pytest.mark.parametrize(
        "event_type",
        ["payment.succeeded", "payment.failed", "message.delivered", "message.failed", "customer.responded"],
    )
    def test_outcome(self, event_type):
        assert classify_event_type(event_type) == "outcome"

    def test_unknown(self):
        assert classify_event_type("checkout.abandoned") == "unknown"

    def test_none(self):
        assert classify_event_type(None) == "unknown"


class TestValidatePaymentFailureEvent:
    def test_valid_payload_has_no_errors(self):
        assert validate_payment_failure_event(valid_payment_failure_payload()) == []

    def test_missing_customer_id(self):
        payload = valid_payment_failure_payload()
        del payload["customer_id"]
        errors = validate_payment_failure_event(payload)
        assert any("customer_id" in e for e in errors)

    def test_missing_multiple_fields_reports_all(self):
        payload = {"event_type": "subscription.payment_failed"}
        errors = validate_payment_failure_event(payload)
        assert any("customer_id" in e for e in errors)
        assert any("subscription_id" in e for e in errors)
        assert any("amount_minor" in e for e in errors)
        assert any("currency" in e for e in errors)
        assert any("occurred_at" in e for e in errors)

    @pytest.mark.parametrize("amount", [-1, -500])
    def test_negative_amount_rejected(self, amount):
        payload = valid_payment_failure_payload(amount_minor=amount)
        errors = validate_payment_failure_event(payload)
        assert any("amount_minor" in e for e in errors)

    def test_null_amount_rejected(self):
        payload = valid_payment_failure_payload(amount_minor=None)
        errors = validate_payment_failure_event(payload)
        assert any("amount_minor" in e for e in errors)

    def test_string_amount_rejected(self):
        payload = valid_payment_failure_payload(amount_minor="a lot of money")
        errors = validate_payment_failure_event(payload)
        assert any("amount_minor" in e for e in errors)

    def test_boolean_amount_rejected(self):
        # bool is a subclass of int in Python; must not sneak through.
        payload = valid_payment_failure_payload(amount_minor=True)
        errors = validate_payment_failure_event(payload)
        assert any("amount_minor" in e for e in errors)

    def test_unknown_event_type_flagged_by_validator_too(self):
        payload = valid_payment_failure_payload(event_type="checkout.abandoned")
        errors = validate_payment_failure_event(payload)
        assert any("event_type" in e for e in errors)

    def test_invalid_currency_rejected(self):
        payload = valid_payment_failure_payload(currency="DOGE")
        errors = validate_payment_failure_event(payload)
        assert any("currency" in e for e in errors)

    def test_invalid_decline_code_rejected(self):
        payload = valid_payment_failure_payload(decline_code="card_ate_the_dog")
        errors = validate_payment_failure_event(payload)
        assert any("decline_code" in e for e in errors)

    def test_invalid_occurred_at_rejected(self):
        payload = valid_payment_failure_payload(occurred_at="not-a-timestamp")
        errors = validate_payment_failure_event(payload)
        assert any("occurred_at" in e for e in errors)

    def test_zero_attempt_number_rejected(self):
        payload = valid_payment_failure_payload(attempt_number=0)
        errors = validate_payment_failure_event(payload)
        assert any("attempt_number" in e for e in errors)

    def test_non_dict_payload_rejected(self):
        errors = validate_payment_failure_event("not a dict")  # type: ignore[arg-type]
        assert len(errors) == 1
        assert "payload must be a JSON object" in errors[0]


class TestValidateOutcomeEvent:
    def test_valid_payload_has_no_errors(self):
        assert validate_outcome_event(valid_outcome_payload()) == []

    def test_execution_id_optional(self):
        payload = valid_outcome_payload()
        del payload["execution_id"]
        assert validate_outcome_event(payload) == []

    def test_blank_execution_id_rejected(self):
        payload = valid_outcome_payload(execution_id="   ")
        errors = validate_outcome_event(payload)
        assert any("execution_id" in e for e in errors)

    def test_unknown_event_type_rejected(self):
        payload = valid_outcome_payload(event_type="subscription.payment_failed")
        errors = validate_outcome_event(payload)
        assert any("event_type" in e for e in errors)

    def test_missing_amount_minor(self):
        payload = valid_outcome_payload()
        del payload["amount_minor"]
        errors = validate_outcome_event(payload)
        assert any("amount_minor" in e for e in errors)
