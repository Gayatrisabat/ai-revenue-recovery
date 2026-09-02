from __future__ import annotations

from datetime import datetime, timezone

import pytest

from domain.enums import CaseState
from ingestion.errors import DeadLetterNotFoundError
from ingestion.signature import canonical_bytes

NOW = datetime(2026, 8, 23, 10, 30, 1, tzinfo=timezone.utc)


def payment_failed_payload(**overrides):
    payload = {
        "event_id": "evt_01",
        "event_type": "subscription.payment_failed",
        "occurred_at": "2026-08-23T10:30:00Z",
        "source": "mock_gateway",
        "customer_id": "cus_synthetic_001",
        "subscription_id": "sub_synthetic_001",
        "amount_minor": 149900,
        "currency": "INR",
        "decline_code": "insufficient_funds",
        "payment_method_type": "card",
        "payment_method_fingerprint": "pm_fp_synthetic_001",
        "attempt_number": 1,
        "metadata": {},
    }
    payload.update(overrides)
    return payload


def outcome_payload(**overrides):
    payload = {
        "event_id": "evt_out_01",
        "event_type": "payment.succeeded",
        "occurred_at": "2026-08-24T09:00:00Z",
        "source": "mock_gateway",
        "customer_id": "cus_synthetic_001",
        "subscription_id": "sub_synthetic_001",
        "amount_minor": 149900,
        "currency": "INR",
        "execution_id": "exec_001",
    }
    payload.update(overrides)
    return payload


def submit(service, verifier, payload, *, source="mock_gateway", signature=None, now=NOW):
    sig = signature if signature is not None else verifier.sign(canonical_bytes(payload))
    return service.ingest_event(payload, signature=sig, source=source, now=now)


# ---------------------------------------------------------------------------
# AT-01 Valid Event Ingestion
# ---------------------------------------------------------------------------


class TestValidEventIngestion:
    def test_raw_event_persisted_before_downstream(self, service, verifier, db):
        submit(service, verifier, payment_failed_payload())
        assert len(db.raw_events) == 1
        record = db.raw_events.all()[0]
        assert record.event_id == "evt_01"
        assert record.family == "payment_failure"
        assert record.is_duplicate is False

    def test_result_status_is_ingested(self, service, verifier):
        result = submit(service, verifier, payment_failed_payload())
        assert result.status == "ingested"
        assert result.family == "payment_failure"

    def test_case_created_with_normalized_state(self, service, verifier, db):
        result = submit(service, verifier, payment_failed_payload())
        case = db.cases.get(result.case_id)
        assert case is not None
        assert case.state == CaseState.NORMALIZED

    def test_case_amount_and_currency_match_event(self, service, verifier, db):
        result = submit(service, verifier, payment_failed_payload(amount_minor=99900, currency="INR"))
        case = db.cases.get(result.case_id)
        assert case.principal_amount_minor == 99900
        assert case.currency.value == "INR"

    def test_event_received_and_normalized_audit_events_appended(self, service, verifier, db):
        result = submit(service, verifier, payment_failed_payload())
        case_audit = db.audit_log.for_case(result.case_id)
        event_types = [e.event_type for e in case_audit]
        assert "EVENT_RECEIVED" in event_types
        assert "EVENT_NORMALIZED" in event_types
        assert event_types.index("EVENT_RECEIVED") < event_types.index("EVENT_NORMALIZED")

    def test_no_downstream_action_taken(self, service, verifier, db):
        # "without executing actions": the case must stop at NORMALIZED,
        # never reach eligibility/diagnosis/candidate/execution states.
        result = submit(service, verifier, payment_failed_payload())
        case = db.cases.get(result.case_id)
        assert case.state == CaseState.NORMALIZED
        assert case.state not in (
            CaseState.ELIGIBILITY_CHECKED,
            CaseState.DIAGNOSED,
            CaseState.CANDIDATES_GENERATED,
            CaseState.ACTION_SCHEDULED,
        )


# ---------------------------------------------------------------------------
# AT-02 Duplicate Event Handling
# ---------------------------------------------------------------------------


class TestDuplicateEventHandling:
    def test_second_submission_is_marked_duplicate(self, service, verifier, db):
        first = submit(service, verifier, payment_failed_payload())
        second = submit(service, verifier, payment_failed_payload())
        assert first.status == "ingested"
        assert second.status == "duplicate"

    def test_no_second_case_created(self, service, verifier, db):
        submit(service, verifier, payment_failed_payload())
        submit(service, verifier, payment_failed_payload())
        assert len(db.cases) == 1

    def test_duplicate_event_still_preserved_in_raw_events(self, service, verifier, db):
        submit(service, verifier, payment_failed_payload())
        submit(service, verifier, payment_failed_payload())
        records = db.raw_events.by_event_id("evt_01")
        assert len(records) == 2
        assert records[0].is_duplicate is False
        assert records[1].is_duplicate is True

    def test_event_deduplicated_audit_event_appended(self, service, verifier, db):
        first = submit(service, verifier, payment_failed_payload())
        submit(service, verifier, payment_failed_payload())
        event_types = [e.event_type for e in db.audit_log.for_case(first.case_id)]
        assert "EVENT_DEDUPLICATED" in event_types

    def test_original_case_state_unchanged_after_duplicate(self, service, verifier, db):
        first = submit(service, verifier, payment_failed_payload())
        case_before = db.cases.get(first.case_id)
        submit(service, verifier, payment_failed_payload())
        case_after = db.cases.get(first.case_id)
        assert case_before.state == case_after.state == CaseState.NORMALIZED

    def test_ten_identical_submissions_yield_one_case(self, service, verifier, db):
        for _ in range(10):
            submit(service, verifier, payment_failed_payload())
        assert len(db.cases) == 1
        assert len(db.raw_events) == 10
        assert len(db.dedup) == 1


# ---------------------------------------------------------------------------
# AT-03 Invalid Event Handling
# ---------------------------------------------------------------------------


class TestInvalidEventHandling:
    def test_missing_required_field_rejected(self, service, verifier, db):
        payload = payment_failed_payload()
        del payload["customer_id"]
        result = submit(service, verifier, payload)
        assert result.status == "dead_lettered"
        assert result.dead_letter_reason == "schema_invalid"
        assert len(db.cases) == 0

    def test_missing_field_persisted_in_dead_letter_table_with_error(self, service, verifier, db):
        payload = payment_failed_payload()
        del payload["customer_id"]
        result = submit(service, verifier, payload)
        record = db.dead_letters.get(result.dead_letter_id)
        assert record is not None
        assert any("customer_id" in e for e in record.errors)

    def test_no_downstream_processing_on_missing_field(self, service, verifier, db):
        payload = payment_failed_payload()
        del payload["customer_id"]
        submit(service, verifier, payload)
        assert len(db.audit_log) == 0
        assert len(db.raw_events) == 0

    @pytest.mark.parametrize("bad_amount", [-500, None, "a lot of money"])
    def test_invalid_amount_types_rejected(self, service, verifier, db, bad_amount):
        payload = payment_failed_payload(amount_minor=bad_amount)
        result = submit(service, verifier, payload)
        assert result.status == "dead_lettered"
        record = db.dead_letters.get(result.dead_letter_id)
        assert any("amount_minor" in e for e in record.errors)
        assert len(db.cases) == 0

    def test_unknown_event_type_rejected(self, service, verifier, db):
        payload = payment_failed_payload(event_type="checkout.abandoned")
        del payload["decline_code"]
        result = submit(service, verifier, payload)
        assert result.status == "dead_lettered"
        assert result.dead_letter_reason == "unknown_event_type"
        assert len(db.cases) == 0

    def test_unknown_event_type_persisted_in_dead_letter_table(self, service, verifier, db):
        payload = payment_failed_payload(event_type="checkout.abandoned")
        del payload["decline_code"]
        result = submit(service, verifier, payload)
        record = db.dead_letters.get(result.dead_letter_id)
        assert record is not None
        assert record.reason == "unknown_event_type"


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


class TestSignatureVerification:
    def test_valid_signature_proceeds_normally(self, service, verifier, db):
        result = submit(service, verifier, payment_failed_payload())
        assert result.status == "ingested"

    def test_invalid_signature_is_quarantined(self, service, verifier, db):
        result = submit(service, verifier, payment_failed_payload(), signature="not-a-real-signature")
        assert result.status == "dead_lettered"
        assert result.dead_letter_reason == "signature_invalid"

    def test_invalid_signature_never_touches_raw_events_or_cases(self, service, verifier, db):
        submit(service, verifier, payment_failed_payload(), signature="not-a-real-signature")
        assert len(db.raw_events) == 0
        assert len(db.cases) == 0


# ---------------------------------------------------------------------------
# Case creation vs. update for the same open episode
# ---------------------------------------------------------------------------


class TestCaseCreationAndUpdate:
    def test_second_failure_same_customer_and_subscription_updates_existing_case(
        self, service, verifier, db
    ):
        first = submit(
            service,
            verifier,
            payment_failed_payload(event_id="evt_ep_01", attempt_number=1),
        )
        second = submit(
            service,
            verifier,
            payment_failed_payload(event_id="evt_ep_02", attempt_number=2, amount_minor=150000),
        )
        assert first.case_id == second.case_id
        assert len(db.cases) == 1
        case = db.cases.get(first.case_id)
        assert case.retry_count_episode == 1
        assert case.principal_amount_minor == 150000

    def test_case_updated_audit_event_appended_on_update(self, service, verifier, db):
        first = submit(service, verifier, payment_failed_payload(event_id="evt_ep_01"))
        submit(service, verifier, payment_failed_payload(event_id="evt_ep_02"))
        event_types = [e.event_type for e in db.audit_log.for_case(first.case_id)]
        assert "CASE_UPDATED" in event_types

    def test_different_customer_gets_a_different_case(self, service, verifier, db):
        first = submit(service, verifier, payment_failed_payload(event_id="evt_a", customer_id="cus_a"))
        second = submit(service, verifier, payment_failed_payload(event_id="evt_b", customer_id="cus_b"))
        assert first.case_id != second.case_id
        assert len(db.cases) == 2


# ---------------------------------------------------------------------------
# Outcome events
# ---------------------------------------------------------------------------


class TestOutcomeEventIngestion:
    def test_outcome_event_matches_open_case(self, service, verifier, db):
        failure = submit(service, verifier, payment_failed_payload())
        result = submit(service, verifier, outcome_payload())
        assert result.status == "ingested"
        assert result.family == "outcome"
        assert result.case_matched is True
        assert result.case_id == failure.case_id

    def test_outcome_event_does_not_change_case_state(self, service, verifier, db):
        failure = submit(service, verifier, payment_failed_payload())
        submit(service, verifier, outcome_payload())
        case = db.cases.get(failure.case_id)
        assert case.state == CaseState.NORMALIZED

    def test_outcome_event_appends_outcome_received_audit(self, service, verifier, db):
        failure = submit(service, verifier, payment_failed_payload())
        submit(service, verifier, outcome_payload())
        event_types = [e.event_type for e in db.audit_log.for_case(failure.case_id)]
        assert "OUTCOME_EVENT_RECEIVED" in event_types

    def test_outcome_event_with_no_matching_case_is_still_persisted(self, service, verifier, db):
        result = submit(
            service,
            verifier,
            outcome_payload(customer_id="cus_no_such_case", subscription_id="sub_no_such_case"),
        )
        assert result.status == "ingested"
        assert result.case_matched is False
        assert result.case_id is None
        assert len(db.raw_events) == 1

    def test_outcome_event_missing_amount_is_dead_lettered(self, service, verifier, db):
        payload = outcome_payload()
        del payload["amount_minor"]
        result = submit(service, verifier, payload)
        assert result.status == "dead_lettered"


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


class TestReplay:
    def test_replay_unknown_dead_letter_id_raises(self, service):
        with pytest.raises(DeadLetterNotFoundError):
            service.replay_dead_letter("dlq_does_not_exist")

    def test_replay_with_corrected_payload_succeeds(self, service, verifier, db):
        payload = payment_failed_payload()
        del payload["customer_id"]
        first = submit(service, verifier, payload)
        assert first.status == "dead_lettered"

        fixed_payload = payment_failed_payload()  # has customer_id restored
        replay_result = service.replay_dead_letter(
            first.dead_letter_id,
            payload_override=fixed_payload,
            signature_override=verifier.sign(canonical_bytes(fixed_payload)),
            now=NOW,
        )
        assert replay_result.status == "ingested"
        assert len(db.cases) == 1

        record = db.dead_letters.get(first.dead_letter_id)
        assert record.resolved is True
        assert record.replay_count == 1

    def test_replay_without_fix_still_fails_and_is_not_marked_resolved(self, service, verifier, db):
        payload = payment_failed_payload()
        del payload["customer_id"]
        first = submit(service, verifier, payload)

        replay_result = service.replay_dead_letter(first.dead_letter_id, now=NOW)
        assert replay_result.status == "dead_lettered"

        record = db.dead_letters.get(first.dead_letter_id)
        assert record.resolved is False
        assert record.replay_count == 1
        # The failed replay creates its own new dead-letter entry too.
        assert len(db.dead_letters) == 2

    def test_replay_of_signature_failure_with_corrected_signature_succeeds(
        self, service, verifier, db
    ):
        payload = payment_failed_payload()
        first = submit(service, verifier, payload, signature="bad-signature")
        assert first.status == "dead_lettered"
        assert first.dead_letter_reason == "signature_invalid"

        replay_result = service.replay_dead_letter(
            first.dead_letter_id,
            signature_override=verifier.sign(canonical_bytes(payload)),
            now=NOW,
        )
        assert replay_result.status == "ingested"
