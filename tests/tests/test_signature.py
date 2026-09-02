from __future__ import annotations

from ingestion.signature import LocalTestSignatureVerifier, canonical_bytes


class TestCanonicalBytes:
    def test_key_order_does_not_affect_bytes(self):
        a = canonical_bytes({"b": 1, "a": 2})
        b = canonical_bytes({"a": 2, "b": 1})
        assert a == b


class TestLocalTestSignatureVerifier:
    def test_valid_signature_verifies(self):
        verifier = LocalTestSignatureVerifier()
        payload = canonical_bytes({"event_id": "evt_01"})
        signature = verifier.sign(payload)
        assert verifier.verify(payload, signature, source="mock_gateway") is True

    def test_tampered_payload_fails_verification(self):
        verifier = LocalTestSignatureVerifier()
        signature = verifier.sign(canonical_bytes({"event_id": "evt_01"}))
        tampered = canonical_bytes({"event_id": "evt_02"})
        assert verifier.verify(tampered, signature, source="mock_gateway") is False

    def test_wrong_secret_fails_verification(self):
        signer = LocalTestSignatureVerifier(secret="secret_a")
        checker = LocalTestSignatureVerifier(secret="secret_b")
        payload = canonical_bytes({"event_id": "evt_01"})
        signature = signer.sign(payload)
        assert checker.verify(payload, signature, source="mock_gateway") is False

    def test_empty_signature_fails(self):
        verifier = LocalTestSignatureVerifier()
        payload = canonical_bytes({"event_id": "evt_01"})
        assert verifier.verify(payload, "", source="mock_gateway") is False

    def test_untrusted_source_fails_even_with_valid_signature(self):
        verifier = LocalTestSignatureVerifier(trusted_sources={"mock_gateway"})
        payload = canonical_bytes({"event_id": "evt_01"})
        signature = verifier.sign(payload)
        assert verifier.verify(payload, signature, source="untrusted_source") is False

    def test_trusted_source_with_valid_signature_succeeds(self):
        verifier = LocalTestSignatureVerifier(trusted_sources={"mock_gateway"})
        payload = canonical_bytes({"event_id": "evt_01"})
        signature = verifier.sign(payload)
        assert verifier.verify(payload, signature, source="mock_gateway") is True
