"""Loaders for the two configuration files this engine reads.

  - policies/recovery-policy.yaml: business-owner-approved policy
    values (docs/policy-decisions.md). PolicyConfig is a 1:1 mapping of
    that file's keys -- nothing here is invented.

  - policies/economic-tables.yaml: action costs, risk penalties, and
    success-probability estimates the expected-value formula needs.
    These are explicitly marked DEMO in that file: docs/policy-
    decisions.md §11-13 lists them as still-open `__APPROVE__` items,
    not yet confirmed by the business owner. The engine still needs
    *some* numbers to compute a demo expected-value score, so this
    module loads them from a clearly-separate, clearly-labeled file
    rather than inventing them silently inside code.

Both are plain dataclasses with no framework dependency beyond PyYAML.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from domain.enums import DeclineCode, RiskTier

from .errors import PolicyConfigError

REQUIRED_POLICY_FIELDS = (
    "policy_version",
    "currency",
    "max_payment_retries_per_episode",
    "max_customer_contacts_per_week",
    "cooldown_hours_between_contacts",
    "attribution_window_hours",
    "minimum_expected_net_recovery_minor",
    "high_value_approval_threshold_minor",
    "stop_on_opt_out",
    "stop_on_dispute",
    "stop_on_paid_subscription",
    "stop_on_canceled_subscription",
    "escalate_on_unknown_decline",
    "real_money_execution_enabled",
    "real_messaging_enabled",
)


@dataclass(frozen=True)
class PolicyConfig:
    policy_version: str
    currency: str
    max_payment_retries_per_episode: int
    max_customer_contacts_per_week: int
    cooldown_hours_between_contacts: int
    attribution_window_hours: int
    minimum_expected_net_recovery_minor: int
    high_value_approval_threshold_minor: int
    stop_on_opt_out: bool
    stop_on_dispute: bool
    stop_on_paid_subscription: bool
    stop_on_canceled_subscription: bool
    escalate_on_unknown_decline: bool
    real_money_execution_enabled: bool
    real_messaging_enabled: bool

    def __post_init__(self) -> None:
        # These four are non-negotiable safety invariants, not just
        # config values -- docs/policy-decisions.md §7 / §16.
        if not self.stop_on_opt_out:
            raise PolicyConfigError("stop_on_opt_out must be true")
        if not self.stop_on_dispute:
            raise PolicyConfigError("stop_on_dispute must be true")
        if self.real_money_execution_enabled:
            raise PolicyConfigError(
                "real_money_execution_enabled must be false for this MVP engine"
            )
        if self.real_messaging_enabled:
            raise PolicyConfigError(
                "real_messaging_enabled must be false for this MVP engine"
            )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PolicyConfig":
        missing = [f for f in REQUIRED_POLICY_FIELDS if f not in data]
        if missing:
            raise PolicyConfigError(f"policy config missing required fields: {missing}")
        return cls(**{f: data[f] for f in REQUIRED_POLICY_FIELDS})


def load_policy_config(path: str | Path) -> PolicyConfig:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise PolicyConfigError(f"{path} did not parse to a mapping")
    return PolicyConfig.from_dict(data)


# ---------------------------------------------------------------------------
# Economic tables (demo-only cost/risk/probability inputs)
# ---------------------------------------------------------------------------

# Candidate keys are the exact fixed candidate_ids required by the task,
# not raw ActionType enum values (two of them -- the 24h and 72h retries
# -- share ActionType.RETRY_PAYMENT but differ in parameters).
RETRY_24H = "retry_after_24_hours"
RETRY_72H = "retry_after_72_hours"
SEND_EMAIL = "send_approved_email_template_01"
OFFER_ALT_METHOD = "offer_approved_alternate_method"
ESCALATE = "escalate_to_human"
STOP_PURSUIT = "stop_pursuit"

ALL_CANDIDATE_KEYS = (RETRY_24H, RETRY_72H, SEND_EMAIL, OFFER_ALT_METHOD, ESCALATE, STOP_PURSUIT)
RETRY_CANDIDATE_KEYS = (RETRY_24H, RETRY_72H)
CONTACT_CANDIDATE_KEYS = (SEND_EMAIL, OFFER_ALT_METHOD)
# "Non-contact" per AT-07 #2: candidates a contact-cap/cooldown stop
# never blocks. Excludes stop_pursuit, which is the null/no-op action
# and never itself keeps a case out of STOPPED_BY_POLICY.
NON_CONTACT_CANDIDATE_KEYS = (RETRY_24H, RETRY_72H, ESCALATE)


@dataclass(frozen=True)
class ActionCostEntry:
    action_cost_minor: int
    operational_cost_minor: int


@dataclass(frozen=True)
class EconomicTables:
    schema_version: str
    action_costs: dict[str, ActionCostEntry]
    risk_penalty_minor_by_risk_tier: dict[RiskTier, int]
    success_probability_by_decline_code: dict[DeclineCode, dict[str, float]]
    escalation_success_probability: float

    def cost(self, candidate_key: str) -> ActionCostEntry:
        try:
            return self.action_costs[candidate_key]
        except KeyError as exc:
            raise PolicyConfigError(f"no action cost entry for {candidate_key!r}") from exc

    def risk_penalty(self, risk_tier: RiskTier) -> int:
        return self.risk_penalty_minor_by_risk_tier.get(risk_tier, 0)

    def success_probability(self, root_cause: DeclineCode, candidate_key: str) -> float:
        by_code = self.success_probability_by_decline_code.get(root_cause, {})
        return by_code.get(candidate_key, 0.0)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EconomicTables":
        try:
            actions_raw = data["actions"]
            risk_raw = data["risk_penalty_minor"]
            probs_raw = data["success_probability_by_decline_code"]
            escalation_probability = data["escalation_success_probability"]
        except KeyError as exc:
            raise PolicyConfigError(f"economic tables missing required section: {exc}") from exc

        action_costs = {
            key: ActionCostEntry(
                action_cost_minor=entry["action_cost_minor"],
                operational_cost_minor=entry["operational_cost_minor"],
            )
            for key, entry in actions_raw.items()
        }
        risk_penalty = {RiskTier(tier): minor for tier, minor in risk_raw.items()}
        success_probability = {
            DeclineCode(code): dict(candidate_probs) for code, candidate_probs in probs_raw.items()
        }
        return cls(
            schema_version=data.get("schema_version", "unknown"),
            action_costs=action_costs,
            risk_penalty_minor_by_risk_tier=risk_penalty,
            success_probability_by_decline_code=success_probability,
            escalation_success_probability=escalation_probability,
        )


def load_economic_tables(path: str | Path) -> EconomicTables:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise PolicyConfigError(f"{path} did not parse to a mapping")
    return EconomicTables.from_dict(data)
