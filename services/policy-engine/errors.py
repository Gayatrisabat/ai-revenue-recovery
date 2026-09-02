from __future__ import annotations


class PolicyEngineError(Exception):
    """Base class for policy/economic engine errors."""


class PolicyConfigError(PolicyEngineError):
    """Raised when policies/recovery-policy.yaml or the economic tables
    file is missing a required field or fails a safety invariant check
    (e.g. a stop_on_* flag that isn't `true`)."""


class AmountIntegrityError(PolicyEngineError):
    """Raised if a candidate's amount ever fails to equal the case's
    authorized recoverable amount. Should be unreachable through the
    normal generator (which always sets it from the case) -- this
    exists to catch tampering or a future bug, per docs/revised-
    architecture.md §8.1 "Amount integrity"."""

    def __init__(self, candidate_amount_minor: int, case_amount_minor: int):
        self.candidate_amount_minor = candidate_amount_minor
        self.case_amount_minor = case_amount_minor
        super().__init__(
            f"Amount integrity violation: candidate amount "
            f"{candidate_amount_minor} != authorized recoverable amount "
            f"{case_amount_minor}"
        )
