"""Domain layer for the AI Revenue Recovery MVP.

This package contains ONLY:
  - typed domain models (raw events, normalized events, recovery cases,
    diagnoses, action candidates, LLM decisions, executions, outcomes,
    revenue ledger entries, audit events, policy versions)
  - the explicit recovery-case state machine (valid + forbidden transitions)

It deliberately contains no LLM client, no real payment/messaging
integration, and no external API calls. See docs/revised-architecture.md
and docs/acceptance-tests.md for the authoritative source this was built
against.
"""
