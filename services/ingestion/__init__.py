"""Ingestion layer for the AI Revenue Recovery MVP.

Scope (see docs/revised-architecture.md §6 "Detection and reliability
layer" and docs/acceptance-tests.md AT-01/AT-02/AT-03):

  1. verify the gateway signature or trusted-source credential;
  2. validate the event against the canonical schema;
  3. use event_id as an idempotency key;
  4. persist the raw event before downstream processing;
  5. create or update the corresponding recovery case;
  6. reject or quarantine malformed events without executing an action.

This package intentionally stops at RECEIVED -> NORMALIZED. It does not
run eligibility checks, diagnosis, candidate generation, LLM selection,
or execution -- those are later planes in the architecture and are out
of scope here. No real payment gateway, messaging provider, or LLM is
contacted anywhere in this package.
"""
