"""Local event simulator.

Loads JSON fixtures (single events or batches) and submits them through
an IngestionService, standing in for a real payment gateway / messaging
webhook sender. Never makes a network call.

Batch fixtures are a JSON array of "batch entries":

    {
      "event": { ... canonical event payload ... },
      "source": "mock_gateway",              # optional, default below
      "signature": "<precomputed hex>",       # optional
      "received_offset_seconds": 5            # optional, see below
    }

`received_offset_seconds` lets a fixture describe delayed and
out-of-order arrival deliberately: it is the simulated *ingestion*
clock offset from the batch's `base_time`, which can be smaller than,
larger than, or unrelated to the differences between events'
`occurred_at` fields. This is what makes it possible to construct a
fixture where event B's `occurred_at` is earlier than event A's, but B
is still submitted to the service after A -- i.e. delayed / out of
order -- while both remain visible, in arrival order, in the raw event
store afterwards (see ingestion/stores.py RawEventStore).

If `signature` is omitted, the simulator signs the event itself using
its own LocalTestSignatureVerifier -- this is a *test* convenience, not
something a real gateway integration would ever do (a real adapter
receives an already-signed payload).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .service import IngestionService, IngestResult
from .signature import LocalTestSignatureVerifier, canonical_bytes

DEFAULT_SOURCE = "mock_gateway"


@dataclass
class BatchEntry:
    event: dict[str, Any]
    source: str = DEFAULT_SOURCE
    signature: str | None = None
    received_offset_seconds: float | None = None
    label: str | None = None  # human-readable tag for test assertions


@dataclass
class BatchRunResult:
    entries: list[BatchEntry]
    results: list[IngestResult] = field(default_factory=list)

    def by_label(self, label: str) -> IngestResult:
        for entry, result in zip(self.entries, self.results):
            if entry.label == label:
                return result
        raise KeyError(f"no batch entry with label {label!r}")


class LocalEventSimulator:
    """Loads fixtures and drives them through an IngestionService.

    Uses its own LocalTestSignatureVerifier purely to *sign* fixtures
    that don't already carry a signature. The IngestionService it talks
    to should normally be constructed with a matching
    LocalTestSignatureVerifier (or one sharing the same secret) so those
    auto-generated signatures verify -- tests that want a signature
    failure simply pass an explicit bad `signature` in the fixture.
    """

    def __init__(self, signer: LocalTestSignatureVerifier | None = None):
        self.signer = signer or LocalTestSignatureVerifier()

    # -- fixture loading --------------------------------------------------

    @staticmethod
    def load_event(path: str | Path) -> dict[str, Any]:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def load_batch(path: str | Path) -> list[BatchEntry]:
        with open(path, "r", encoding="utf-8") as f:
            raw_entries = json.load(f)
        return [
            BatchEntry(
                event=raw["event"],
                source=raw.get("source", DEFAULT_SOURCE),
                signature=raw.get("signature"),
                received_offset_seconds=raw.get("received_offset_seconds"),
                label=raw.get("label"),
            )
            for raw in raw_entries
        ]

    # -- signing ------------------------------------------------------

    def sign(self, event: dict[str, Any]) -> str:
        return self.signer.sign(canonical_bytes(event))

    # -- submission -----------------------------------------------------

    def submit_event(
        self,
        service: IngestionService,
        event: dict[str, Any],
        *,
        source: str = DEFAULT_SOURCE,
        signature: str | None = None,
        now: datetime | None = None,
    ) -> IngestResult:
        """Submit a single event fixture, signing it if no signature given."""
        sig = signature if signature is not None else self.sign(event)
        return service.ingest_event(event, signature=sig, source=source, now=now)

    def run_batch(
        self,
        service: IngestionService,
        batch: list[BatchEntry],
        *,
        base_time: datetime | None = None,
    ) -> BatchRunResult:
        """Submit every entry in list order (== simulated arrival order).

        Entries are never reordered, deduplicated, or dropped before
        submission -- each one reaches IngestionService.ingest_event()
        exactly once, in the order given, regardless of what its
        `occurred_at` says. This is what "preserves" delayed and
        out-of-order events for testing: the batch's arrival order is
        the ground truth, not a sort by occurred_at.
        """
        base_time = base_time or datetime(2026, 8, 23, 9, 0, 0, tzinfo=timezone.utc)
        run_result = BatchRunResult(entries=list(batch))
        for index, entry in enumerate(batch):
            offset = entry.received_offset_seconds
            if offset is None:
                offset = index  # default: one second apart, in list order
            received_at = base_time + timedelta(seconds=offset)
            result = self.submit_event(
                service,
                entry.event,
                source=entry.source,
                signature=entry.signature,
                now=received_at,
            )
            run_result.results.append(result)
        return run_result
