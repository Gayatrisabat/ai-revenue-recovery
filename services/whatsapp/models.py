"""Typed data models for the WhatsApp communication channel.

Plain, framework-free dataclasses matching the data contracts in
docs/whatsapp-followup-addon.md. No ORM, no network, no provider SDK.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# ---------------------------------------------------------------------------
# Approved template definition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ApprovedTemplate:
    """An approved WhatsApp message template.

    Templates are the only message type available outside a customer-service
    window (docs/whatsapp-followup-addon.md, Meta reference [2]).
    """

    template_name: str
    language: str
    allowed_variables: tuple[str, ...]
    body_text: str
    category: str = "UTILITY"

    def __post_init__(self) -> None:
        if not self.template_name or not self.template_name.strip():
            raise ValueError("template_name must be a non-empty string")
        if not self.body_text or not self.body_text.strip():
            raise ValueError("body_text must be a non-empty string")


# ---------------------------------------------------------------------------
# Outbound candidate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OutboundCandidate:
    """An approved outbound WhatsApp message candidate.

    Created only by deterministic policy code, never by the LLM.
    The LLM receives the candidate and allowed variables but does not
    receive authority to add variables or modify template_name, amount,
    payment_link, or expiry.
    """

    candidate_id: str
    channel: str  # always "whatsapp"
    template_name: str
    language: str
    allowed_variables: dict[str, str]
    customer_id: str
    case_id: str
    policy_version: str
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        for f in ("candidate_id", "channel", "template_name", "customer_id", "case_id"):
            val = getattr(self, f)
            if not isinstance(val, str) or not val.strip():
                raise ValueError(f"{f} must be a non-empty string")
        if self.channel != "whatsapp":
            raise ValueError("channel must be 'whatsapp'")


# ---------------------------------------------------------------------------
# Inbound message (normalized)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InboundMessage:
    """A normalized inbound WhatsApp message from a customer.

    Matches docs/whatsapp-followup-addon.md §"Inbound normalized message".
    Only minimum message content is stored; payment credentials and
    sensitive PII must never appear here.
    """

    event_id: str
    provider: str  # "simulator", "twilio", "meta"
    provider_message_id: str
    customer_id: str
    case_id: str
    received_at: datetime
    message_type: str  # "text"
    text: str
    consent_context: str  # "customer_initiated"
    raw_payload_hash: str

    def __post_init__(self) -> None:
        for f in ("event_id", "provider_message_id", "customer_id", "case_id"):
            val = getattr(self, f)
            if not isinstance(val, str) or not val.strip():
                raise ValueError(f"{f} must be a non-empty string")

    @staticmethod
    def compute_payload_hash(payload: dict[str, Any]) -> str:
        """SHA-256 hash of the raw payload for audit integrity."""
        import json

        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Intent result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IntentResult:
    """Structured output of intent extraction.

    The LLM or deterministic classifier produces this; the policy engine
    decides what action to take. The LLM may not execute actions directly.
    """

    intent: str  # WhatsAppIntent value
    confidence: float
    raw_text: str
    promised_date: str | None = None
    requires_human_review: bool = False
    extraction_method: str = "deterministic"  # "deterministic" or "llm"

    def __post_init__(self) -> None:
        if not isinstance(self.confidence, (int, float)) or isinstance(self.confidence, bool):
            raise ValueError("confidence must be a number")
        if not (0.0 <= float(self.confidence) <= 1.0):
            raise ValueError("confidence must be between 0.0 and 1.0")


# ---------------------------------------------------------------------------
# Policy action (response from policy bridge)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyAction:
    """The deterministic policy engine's response to a classified intent.

    This is what actually happens -- never the raw LLM/intent output.
    """

    action_type: str
    response_template_id: str | None
    personalization_variables: dict[str, str]
    stop_case: bool
    escalate: bool
    reason_codes: tuple[str, ...]
    case_id: str
    customer_id: str

    def __post_init__(self) -> None:
        if not self.action_type or not self.action_type.strip():
            raise ValueError("action_type must be a non-empty string")


# ---------------------------------------------------------------------------
# Communication outcome
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommunicationOutcome:
    """A normalized delivery-status or send-result event.

    Matches docs/whatsapp-followup-addon.md §"Communication outcome".
    """

    event_id: str
    provider_message_id: str
    case_id: str
    status: str  # DeliveryStatus value
    status_received_at: datetime
    channel: str = "whatsapp"
    simulated: bool = True
    error_message: str | None = None

    def __post_init__(self) -> None:
        for f in ("event_id", "provider_message_id", "case_id"):
            val = getattr(self, f)
            if not isinstance(val, str) or not val.strip():
                raise ValueError(f"{f} must be a non-empty string")


# ---------------------------------------------------------------------------
# Composed outbound message (ready to send or dry-run)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ComposedMessage:
    """A fully composed WhatsApp message ready for sending or dry-run.

    The rendered_body is the final text after template rendering.
    This is what would be sent to the provider in non-dry-run mode.
    """

    message_id: str
    case_id: str
    customer_id: str
    to_phone: str
    template_name: str
    rendered_body: str
    variables: dict[str, str]
    dry_run: bool = True
    policy_version: str = ""
    composed_at: datetime | None = None
