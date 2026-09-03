"""WhatsApp communication channel for the AI Revenue Recovery MVP.

This package implements a bounded WhatsApp follow-up and customer-response
adapter, as specified in docs/whatsapp-followup-addon.md. It does NOT
change the frozen recovery architecture (CLAUDE.md).

WhatsApp is inserted only at the communication boundary:

    approved candidate
    → WhatsApp message adapter
    → provider response/status webhook
    → normalized communication event
    → recovery case and audit trail

The inbound customer message path:

    WhatsApp inbound webhook
    → signature/authentication check
    → deduplication
    → message normalization
    → intent extraction/classification
    → policy re-check
    → approved response candidate
    → deterministic template selection
    → final validation
    → WhatsApp reply or human escalation
    → audit event

Key invariants:
  - The LLM may NOT execute actions directly.
  - All intents pass through the deterministic policy engine.
  - opt-out, dispute, already-paid are hard stops.
  - No real messaging by default (REAL_MESSAGING_ENABLED=false).
  - Every operation produces a structured audit event.
"""
