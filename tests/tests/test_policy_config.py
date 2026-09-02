from __future__ import annotations

from pathlib import Path

import pytest
import yaml

POLICY_PATH = Path(__file__).resolve().parent.parent / "policies" / "recovery-policy.yaml"


@pytest.fixture(scope="module")
def policy() -> dict:
    with open(POLICY_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class TestRecoveryPolicyFile:
    def test_file_exists(self):
        assert POLICY_PATH.exists()

    def test_parses_as_a_flat_mapping(self, policy):
        assert isinstance(policy, dict)

    def test_policy_version_and_currency(self, policy):
        assert policy["policy_version"] == "policy_demo_v1"
        assert policy["currency"] == "INR"

    def test_retry_and_contact_limits(self, policy):
        assert policy["max_payment_retries_per_episode"] == 3
        assert policy["max_customer_contacts_per_week"] == 2
        assert policy["cooldown_hours_between_contacts"] == 24

    def test_attribution_window(self, policy):
        assert policy["attribution_window_hours"] == 72

    def test_economic_thresholds(self, policy):
        assert policy["minimum_expected_net_recovery_minor"] == 500
        assert policy["high_value_approval_threshold_minor"] == 100000

    @pytest.mark.parametrize(
        "field_name",
        [
            "stop_on_opt_out",
            "stop_on_dispute",
            "stop_on_paid_subscription",
            "stop_on_canceled_subscription",
            "escalate_on_unknown_decline",
        ],
    )
    def test_safety_stop_rules_are_true(self, policy, field_name):
        # These are the invariants docs/policy-decisions.md §7 marks
        # non-negotiable: a customer who opted out, disputed a charge,
        # already paid, or canceled must never receive another action.
        assert policy[field_name] is True

    @pytest.mark.parametrize(
        "field_name", ["real_money_execution_enabled", "real_messaging_enabled"]
    )
    def test_real_execution_is_disabled_for_the_demo(self, policy, field_name):
        assert policy[field_name] is False

    def test_no_unresolved_approval_placeholders(self, policy):
        # A raw __APPROVE__ marker slipping through would mean this file
        # isn't actually a confirmed policy, contrary to docs/policy-decisions.md.
        assert "__APPROVE__" not in yaml.dump(policy)
