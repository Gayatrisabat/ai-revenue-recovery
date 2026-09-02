"""Signature-verification interface.

Per docs/revised-architecture.md §6, step 1 is "Verify the gateway
signature or trusted source credential." The build runbook is explicit
that the *real* payload/signature scheme must be confirmed from the
actual provider's documentation before any production integration --
that is deliberately not this module's job.

What lives here:

  - SignatureVerifier: the interface every gateway adapter must satisfy.
  - LocalTestSignatureVerifier: a local, synthetic HMAC-based
    implementation used only for fixtures/tests/the local simulator.
    It never calls out to a network and knows nothing about any real
    payment provider's signing scheme.

Swapping in a real provider later means writing one more class that
satisfies SignatureVerifier -- the ingestion service never needs to
change.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Protocol


def canonical_bytes(payload: dict[str, Any]) -> bytes:
    """Deterministic byte serialization used for both signing and
    verifying. Centralized so the simulator (which signs fixtures) and
    the ingestion service (which verifies them) can never drift apart.
    """
    return json.dumps(payload, sort_keys=True, default=str).encode("utf-8")


class SignatureVerifier(Protocol):
    """Anything that can verify an inbound event's authenticity."""

    def verify(self, raw_payload: bytes, signature: str, source: str) -> bool:
        """Return True iff `signature` is a valid signature of `raw_payload`
        for the given `source`. Must never raise for a merely-invalid
        signature -- return False instead. Malformed inputs (wrong types)
        may still raise.
        """
        ...


class LocalTestSignatureVerifier:
    """Synthetic HMAC-SHA256 verifier for local fixtures and tests only.

    NOT connected to any real gateway. `secret` defaults to a fixed,
    publicly-known test value precisely so nobody mistakes this for a
    production credential.
    """

    TEST_SECRET_DEFAULT = "local-test-shared-secret-do-not-use-in-production"

    def __init__(self, secret: str | None = None, trusted_sources: set[str] | None = None):
        self.secret = secret or self.TEST_SECRET_DEFAULT
        # None means "any source is trusted" (useful for quick fixtures);
        # tests that care about untrusted sources pass an explicit set.
        self.trusted_sources = trusted_sources

    def sign(self, raw_payload: bytes) -> str:
        """Helper for fixture generation: produce a valid test signature."""
        return hmac.new(self.secret.encode("utf-8"), raw_payload, hashlib.sha256).hexdigest()

    def verify(self, raw_payload: bytes, signature: str, source: str) -> bool:
        if self.trusted_sources is not None and source not in self.trusted_sources:
            return False
        if not isinstance(signature, str) or not signature:
            return False
        expected = self.sign(raw_payload)
        return hmac.compare_digest(expected, signature)
