"""The deterministic Recovery Policy and Economic Engine.

Implements docs/revised-architecture.md §8 exactly:

  §8.1 Eligibility checks -- eligibility.py
  §8.2 Candidate generation -- candidates.py
  §8.3 Expected-value scoring -- scoring.py

engine.py orchestrates the three into the case-state transitions and
audit events required by docs/acceptance-tests.md AT-04 through AT-10.

No LLM, no real payment or messaging integration lives here. Candidate
*parameters* (delay, amount, template) are fixed entirely by this
engine from versioned policy configuration -- a later, separate LLM
layer may only *select* among the candidates this module produces.
"""
