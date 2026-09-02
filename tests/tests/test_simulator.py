from __future__ import annotations

from datetime import datetime, timedelta, timezone

from domain.enums import CaseState
from ingestion.simulator import LocalEventSimulator


class TestFixtureLoading:
    def test_loads_valid_payment_failure_fixture(self, fixtures_dir):
        event = LocalEventSimulator.load_event(
            fixtures_dir / "valid" / "payment_failed_network_error.json"
        )
        assert event["event_type"] == "subscription.payment_failed"
        assert event["customer_id"] == "cus_synthetic_aarav"

    def test_loads_valid_outcome_fixture(self, fixtures_dir):
        event = LocalEventSimulator.load_event(
            fixtures_dir / "valid" / "outcome_payment_succeeded.json"
        )
        assert event["event_type"] == "payment.succeeded"

    def test_loads_invalid_fixture_as_plain_dict(self, fixtures_dir):
        # Loading is just JSON parsing -- validation happens at ingestion,
        # not at load time, so a "bad" fixture still loads fine here.
        event = LocalEventSimulator.load_event(
            fixtures_dir / "invalid" / "missing_customer_id.json"
        )
        assert "customer_id" not in event

    def test_loads_batch_fixture(self, fixtures_dir):
        batch = LocalEventSimulator.load_batch(
            fixtures_dir / "batches" / "duplicate_delayed_out_of_order_batch.json"
        )
        assert len(batch) == 5
        assert batch[0].label == "vikram_first_failure"
        assert batch[1].event["event_id"] == batch[0].event["event_id"]


class TestSubmitSingleFixture:
    def test_submit_valid_fixture_ingests_successfully(self, service, simulator, fixtures_dir):
        event = LocalEventSimulator.load_event(
            fixtures_dir / "valid" / "payment_failed_network_error.json"
        )
        result = simulator.submit_event(service, event)
        assert result.status == "ingested"
        assert result.family == "payment_failure"

    def test_submit_invalid_fixture_is_dead_lettered(self, service, simulator, fixtures_dir):
        event = LocalEventSimulator.load_event(
            fixtures_dir / "invalid" / "negative_amount.json"
        )
        result = simulator.submit_event(service, event)
        assert result.status == "dead_lettered"
        assert result.dead_letter_reason == "schema_invalid"

    def test_submit_unknown_event_type_fixture_is_dead_lettered(
        self, service, simulator, fixtures_dir
    ):
        event = LocalEventSimulator.load_event(
            fixtures_dir / "invalid" / "unknown_event_type.json"
        )
        result = simulator.submit_event(service, event)
        assert result.status == "dead_lettered"
        assert result.dead_letter_reason == "unknown_event_type"

    def test_auto_signed_fixture_verifies_against_matching_service(
        self, service, simulator, fixtures_dir
    ):
        event = LocalEventSimulator.load_event(
            fixtures_dir / "valid" / "payment_failed_insufficient_funds.json"
        )
        result = simulator.submit_event(service, event)
        # If signing/verifying ever drifted apart this would come back
        # dead_lettered with dead_letter_reason == "signature_invalid".
        assert result.status == "ingested"


class TestRunBatch:
    def test_duplicate_delayed_out_of_order_batch_runs_without_error(
        self, service, simulator, db, fixtures_dir
    ):
        batch = LocalEventSimulator.load_batch(
            fixtures_dir / "batches" / "duplicate_delayed_out_of_order_batch.json"
        )
        run_result = simulator.run_batch(service, batch)
        assert len(run_result.results) == 5

    def test_duplicate_entry_in_batch_is_detected(self, service, simulator, db, fixtures_dir):
        batch = LocalEventSimulator.load_batch(
            fixtures_dir / "batches" / "duplicate_delayed_out_of_order_batch.json"
        )
        run_result = simulator.run_batch(service, batch)
        first = run_result.by_label("vikram_first_failure")
        duplicate = run_result.by_label("vikram_duplicate_resubmit")
        assert first.status == "ingested"
        assert duplicate.status == "duplicate"
        # Exactly one case for Vikram despite two submissions.
        vikram_cases = [c for c in db.cases.all() if c.customer_id == "cus_synthetic_vikram"]
        assert len(vikram_cases) == 1

    def test_delayed_event_is_still_processed_not_dropped(self, service, simulator, db, fixtures_dir):
        batch = LocalEventSimulator.load_batch(
            fixtures_dir / "batches" / "duplicate_delayed_out_of_order_batch.json"
        )
        run_result = simulator.run_batch(service, batch)
        delayed = run_result.by_label("meera_delayed_failure")
        assert delayed.status == "ingested"
        meera_cases = [c for c in db.cases.all() if c.customer_id == "cus_synthetic_meera"]
        assert len(meera_cases) == 1
        assert meera_cases[0].state == CaseState.NORMALIZED

    def test_delayed_event_occurred_at_is_earlier_than_its_arrival_order_implies(
        self, fixtures_dir
    ):
        batch = LocalEventSimulator.load_batch(
            fixtures_dir / "batches" / "duplicate_delayed_out_of_order_batch.json"
        )
        vikram_entry = next(e for e in batch if e.label == "vikram_first_failure")
        meera_entry = next(e for e in batch if e.label == "meera_delayed_failure")
        # Meera's failure *happened* before Vikram's ...
        assert meera_entry.event["occurred_at"] < vikram_entry.event["occurred_at"]
        # ... but arrives at the service much later (delayed webhook).
        assert meera_entry.received_offset_seconds > vikram_entry.received_offset_seconds

    def test_out_of_order_pair_both_preserved_in_raw_events(self, service, simulator, db, fixtures_dir):
        batch = LocalEventSimulator.load_batch(
            fixtures_dir / "batches" / "duplicate_delayed_out_of_order_batch.json"
        )
        run_result = simulator.run_batch(service, batch)
        first_submitted = run_result.by_label("kavya_out_of_order_first_submitted")
        second_submitted = run_result.by_label("kavya_out_of_order_second_submitted")
        assert first_submitted.status == "ingested"
        assert second_submitted.status == "ingested"

        # Both raw events exist, in the order they actually arrived --
        # not resorted by occurred_at.
        kavya_records = [r for r in db.raw_events.all() if r.payload["customer_id"] == "cus_synthetic_kavya"]
        assert len(kavya_records) == 2
        assert kavya_records[0].event_id == "evt_kavya_network_error_attempt_2"
        assert kavya_records[1].event_id == "evt_kavya_network_error_attempt_1"
        # The second-submitted event's occurred_at is chronologically
        # earlier -- proving arrival order and occurred_at order differ.
        assert kavya_records[1].payload["occurred_at"] < kavya_records[0].payload["occurred_at"]

    def test_out_of_order_pair_collapses_into_one_open_episode(
        self, service, simulator, db, fixtures_dir
    ):
        batch = LocalEventSimulator.load_batch(
            fixtures_dir / "batches" / "duplicate_delayed_out_of_order_batch.json"
        )
        simulator.run_batch(service, batch)
        kavya_cases = [c for c in db.cases.all() if c.customer_id == "cus_synthetic_kavya"]
        assert len(kavya_cases) == 1
        assert kavya_cases[0].retry_count_episode == 1

    def test_raw_event_store_preserves_full_arrival_order_across_batch(
        self, service, simulator, db, fixtures_dir
    ):
        batch = LocalEventSimulator.load_batch(
            fixtures_dir / "batches" / "duplicate_delayed_out_of_order_batch.json"
        )
        simulator.run_batch(service, batch)
        arrived_event_ids = [r.event_id for r in db.raw_events.all()]
        assert arrived_event_ids == [
            "evt_vikram_expired_card_01",
            "evt_vikram_expired_card_01",
            "evt_meera_insufficient_funds_delayed_01",
            "evt_kavya_network_error_attempt_2",
            "evt_kavya_network_error_attempt_1",
        ]

    def test_explicit_received_offsets_produce_expected_received_at(
        self, service, simulator, db, fixtures_dir
    ):
        batch = LocalEventSimulator.load_batch(
            fixtures_dir / "batches" / "duplicate_delayed_out_of_order_batch.json"
        )
        base_time = datetime(2026, 8, 23, 9, 0, 0, tzinfo=timezone.utc)
        simulator.run_batch(service, batch, base_time=base_time)
        meera_record = next(
            r for r in db.raw_events.all() if r.event_id == "evt_meera_insufficient_funds_delayed_01"
        )
        assert meera_record.received_at == base_time + timedelta(seconds=300)
