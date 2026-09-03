"""Enumerations for the WhatsApp communication channel.

These enums are specific to the WhatsApp adapter and do not modify
the core domain enums in packages/domain-models/enums.py.
"""

from __future__ import annotations

from enum import Enum


class WhatsAppIntent(str, Enum):
    """Strict intent categories for inbound customer messages.

    Defined in docs/whatsapp-followup-addon.md §Step 2.
    Every inbound message must be classified into exactly one of these.
    """

    RESOLUTION_EXPLANATION = "resolution_explanation"
    UPDATE_PAYMENT_METHOD = "update_payment_method"
    REQUEST_PAYMENT_LINK = "request_payment_link"
    PROMISE_TO_PAY = "promise_to_pay"
    HUMAN_SUPPORT = "human_support"
    ALREADY_PAID = "already_paid"
    DISPUTE = "dispute"
    OPT_OUT = "opt_out"
    UNCLEAR = "unclear"
    PROMPT_INJECTION = "prompt_injection"


class DeliveryStatus(str, Enum):
    """WhatsApp message delivery statuses.

    Mapped from provider-specific statuses (Twilio / Meta) into
    this canonical set, per docs/whatsapp-followup-addon.md.
    """

    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"
    UNDELIVERABLE = "undeliverable"


class MessageDirection(str, Enum):
    """Direction of a WhatsApp message relative to our system."""

    INBOUND = "inbound"
    OUTBOUND = "outbound"


class WhatsAppAuditEventType(str, Enum):
    """Structured audit event types for WhatsApp operations.

    Every WhatsApp operation must produce one of these.
    """

    OUTBOUND_COMPOSED = "WHATSAPP_OUTBOUND_COMPOSED"
    OUTBOUND_DRY_RUN = "WHATSAPP_OUTBOUND_DRY_RUN"
    OUTBOUND_SENT = "WHATSAPP_OUTBOUND_SENT"
    OUTBOUND_FAILED = "WHATSAPP_OUTBOUND_FAILED"
    INBOUND_RECEIVED = "WHATSAPP_INBOUND_RECEIVED"
    INTENT_EXTRACTED = "WHATSAPP_INTENT_EXTRACTED"
    POLICY_CHECKED = "WHATSAPP_POLICY_CHECKED"
    RESPONSE_SENT = "WHATSAPP_RESPONSE_SENT"
    STATUS_RECEIVED = "WHATSAPP_STATUS_RECEIVED"
    DUPLICATE_IGNORED = "WHATSAPP_DUPLICATE_IGNORED"
    OPT_OUT_RECORDED = "WHATSAPP_OPT_OUT_RECORDED"
    DISPUTE_RECORDED = "WHATSAPP_DISPUTE_RECORDED"
    ALREADY_PAID_STOPPED = "WHATSAPP_ALREADY_PAID_STOPPED"
    PROMPT_INJECTION_BLOCKED = "WHATSAPP_PROMPT_INJECTION_BLOCKED"
    ESCALATED_TO_HUMAN = "WHATSAPP_ESCALATED_TO_HUMAN"
    PROMISE_TO_PAY_RECORDED = "WHATSAPP_PROMISE_TO_PAY_RECORDED"
    TEMPLATE_VALIDATION_FAILED = "WHATSAPP_TEMPLATE_VALIDATION_FAILED"
    POLICY_BLOCKED = "WHATSAPP_POLICY_BLOCKED"
