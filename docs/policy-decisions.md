# Policy Decisions — Human-Approved Values

**Authority:** `CLAUDE.md` and `docs/revised-architecture.md`
**Status:** All values in this document are **placeholders** requiring human approval before implementation.

> [!CAUTION]
> Do not implement any policy value that has not been explicitly approved by the business owner. Every `__APPROVE__` marker below must be replaced with a confirmed value. Code must read from this configuration, not from hard-coded constants.

---

## 1. Policy Version

| Field | Placeholder | Notes |
|---|---|---|
| `policy_version` | `__APPROVE__` | Example: `policy_demo_v1`. Every policy change must increment the version. |

---

## 2. Retry Limits

| Field | Placeholder | Notes |
|---|---|---|
| `max_payment_retries_per_episode` | `__APPROVE__` | Maximum number of payment retry attempts for a single recovery case episode. Build-runbook example: `3`. |

**Safety invariant:** A case above the retry cap cannot receive another retry.

---

## 3. Contact Limits

| Field | Placeholder | Notes |
|---|---|---|
| `max_customer_contacts_per_week` | `__APPROVE__` | Maximum customer-facing outreach actions (messages, offers) per rolling 7-day window. Build-runbook example: `2`. |

**Safety invariant:** A case above the contact cap cannot receive another contact.

---

## 4. Cooldown

| Field | Placeholder | Notes |
|---|---|---|
| `cooldown_hours_between_contacts` | `__APPROVE__` | Minimum hours between successive customer-facing contact actions. Build-runbook example: `24`. |

**Safety invariant:** A case inside the cooldown window cannot receive another contact.

---

## 5. Approval Thresholds

| Field | Placeholder | Notes |
|---|---|---|
| `high_value_approval_threshold_minor` | `__APPROVE__` | Amount (in minor units, e.g., paise) above which an action requires human review before execution. Build-runbook example: `100000` (₹1,000). |

---

## 6. Economic Thresholds

| Field | Placeholder | Notes |
|---|---|---|
| `minimum_expected_net_recovery_minor` | `__APPROVE__` | Minimum expected net recovery (in minor units) for an action to be economically justified. Build-runbook example: `500` (₹5). |

**Safety invariant:** An action below the economic threshold must not execute.

**Economic formula (from architecture):**

```
expected_net_recovery
= calibrated_success_probability × recoverable_amount
  − action_cost
  − operational_cost
  − risk_penalty
```

---

## 7. Stop Rules

| Field | Placeholder | Notes |
|---|---|---|
| `stop_on_opt_out` | `__APPROVE__` | Must be `true`. When a customer has opted out, all automated outreach stops immediately. |
| `stop_on_dispute` | `__APPROVE__` | Must be `true`. When a charge is disputed, automated recovery stops and the case is escalated or closed. |
| `stop_on_canceled_subscription` | `__APPROVE__` | Behavior when the subscription is already canceled. |
| `stop_on_already_paid` | `__APPROVE__` | Behavior when the payment has already succeeded. |

**Safety invariants:**
- A customer who opted out cannot receive automated outreach.
- A disputed case cannot receive automated recovery action.
- A canceled or already-paid subscription cannot receive another recovery action.

---

## 8. Unknown-Decline Handling

| Field | Placeholder | Notes |
|---|---|---|
| `escalate_on_unknown_decline` | `__APPROVE__` | Behavior for decline codes not in the deterministic mapping. Build-runbook example: `true` (route to human review or stop). |
| `unknown_decline_fallback_action` | `__APPROVE__` | Which action to take: `escalate_to_human` or `stop_pursuit`. |

**Safety invariant:** An unknown decline category must use the configured fallback behavior.

---

## 9. Cohort Assignment

| Field | Placeholder | Notes |
|---|---|---|
| `treatment_cohort_percentage` | `__APPROVE__` | Percentage of eligible cases assigned to the treatment group. Architecture example: `80` (0–79 range). |
| `holdout_cohort_percentage` | `__APPROVE__` | Percentage assigned to holdout. Architecture example: `20` (80–99 range). |
| `cohort_hash_method` | `stable_hash(customer_id + case_id) % 100` | From architecture §11.1. |

**Safety invariant:** A holdout case cannot receive treatment outreach.

---

## 10. Attribution Window

| Field | Placeholder | Notes |
|---|---|---|
| `attribution_window_hours` | `__APPROVE__` | Maximum hours after an action during which a successful payment is attributed to that action. Architecture example: `72`. |

---

## 11. Action Cost Table

Each candidate action has an associated cost. These costs feed the economic scoring formula.

| Action Type | `action_cost_minor` | `operational_cost_minor` | Notes |
|---|---|---|---|
| `retry_after_24_hours` | `__APPROVE__` | `__APPROVE__` | Cost of a payment retry attempt |
| `retry_after_72_hours` | `__APPROVE__` | `__APPROVE__` | Cost of a delayed payment retry |
| `send_approved_email_template_01` | `__APPROVE__` | `__APPROVE__` | Cost of sending an email |
| `offer_approved_alternate_method` | `__APPROVE__` | `__APPROVE__` | Cost of an alternate-method offer |
| `escalate_to_human` | `__APPROVE__` | `__APPROVE__` | Cost of human review |
| `stop_pursuit` | `0` | `0` | No cost to stop |

---

## 12. Risk Penalty Table

| Condition | `risk_penalty_minor` | Notes |
|---|---|---|
| Standard risk tier | `__APPROVE__` | Default penalty |
| Disputed history (prior disputes) | `__APPROVE__` | Higher penalty for customers with dispute history |
| High retry count | `__APPROVE__` | Increasing penalty with more retries |

---

## 13. Success Probability Overrides

The diagnosis engine provides calibrated probabilities. These are **demo estimates only**.

| Decline Code | `success_probability_now` | `recommended_retry_window` | Notes |
|---|---|---|---|
| `insufficient_funds` | `__APPROVE__` | `__APPROVE__` | Architecture example: `0.18`, `24_to_72_hours` |
| `expired_card` | `__APPROVE__` | `__APPROVE__` | Retry unlikely to succeed; alternate method or escalation preferred |
| `issuer_decline` | `__APPROVE__` | `__APPROVE__` | |
| `network_error` | `__APPROVE__` | `__APPROVE__` | Transient; higher retry probability |
| `do_not_honor` | `__APPROVE__` | `__APPROVE__` | |
| `stolen_card` | `__APPROVE__` | `__APPROVE__` | Automated recovery likely inappropriate |
| `unknown` | `__APPROVE__` | `__APPROVE__` | Uses fallback behavior from §8 above |

> [!WARNING]
> These probabilities are synthetic demonstration values. Do not present them as calibrated production predictions.

---

## 14. Approved Message Templates

Each template must be reviewed and approved before use. The LLM may personalize using only the listed variables.

### Template: `email_template_01`

| Field | Placeholder | Notes |
|---|---|---|
| `template_id` | `email_template_01` | Immutable identifier |
| `template_version` | `__APPROVE__` | Increment on any change |
| `channel` | `email` | |
| `subject` | `__APPROVE__` | Example: `"Action needed: update your payment for {{subscription_name}}"` |
| `body` | `__APPROVE__` | Must not contain pressure language, false urgency, or payment claims the system cannot verify. |
| `allowed_variables` | `__APPROVE__` | Example: `customer_first_name`, `payment_amount_display`, `support_link`, `subscription_name` |

> [!IMPORTANT]
> The system must not invent risky payment claims or pressure language. Template copy requires human approval.

---

## 15. LLM Configuration

| Field | Placeholder | Notes |
|---|---|---|
| `llm_provider` | `__APPROVE__` | e.g., `gemini`, `openai`, or `mock` (default for MVP) |
| `llm_model` | `__APPROVE__` | e.g., `gemini-2.0-flash`, `gpt-4o-mini` |
| `llm_timeout_seconds` | `__APPROVE__` | Maximum wait time before fallback |
| `llm_temperature` | `__APPROVE__` | Recommended: `0.0` or very low for deterministic selection |
| `llm_fallback_strategy` | `__APPROVE__` | `highest_scored_candidate` or `escalate_to_human` |

---

## 16. Execution Safety

| Field | Placeholder | Notes |
|---|---|---|
| `execution_mode` | `mock` | **Must be `mock` for MVP.** Real execution requires separate human approval. |
| `allow_real_payment_retry` | `false` | **Must be `false` for MVP.** |
| `allow_real_messaging` | `false` | **Must be `false` for MVP.** |

> [!CAUTION]
> Changing execution mode to anything other than `mock` requires explicit human approval and is outside the MVP scope.

---

## 17. Case Expiry

| Field | Placeholder | Notes |
|---|---|---|
| `case_expiry_hours` | `__APPROVE__` | Hours after which an unresolved case transitions to `EXPIRED`. |

---

## Approval Checklist

Before implementation begins, the business owner must replace every `__APPROVE__` marker with a confirmed value.

- [ ] Policy version string
- [ ] Max payment retries per episode
- [ ] Max customer contacts per week
- [ ] Cooldown hours between contacts
- [ ] High-value approval threshold
- [ ] Minimum expected net recovery threshold
- [ ] Stop-on-opt-out (confirm `true`)
- [ ] Stop-on-dispute (confirm `true`)
- [ ] Stop-on-canceled-subscription behavior
- [ ] Stop-on-already-paid behavior
- [ ] Unknown-decline escalation behavior
- [ ] Treatment/holdout split percentages
- [ ] Attribution window hours
- [ ] Action cost table (all 6 actions)
- [ ] Risk penalty table
- [ ] Success probability demo values (all 7 codes)
- [ ] Email template subject, body, and allowed variables
- [ ] LLM provider, model, timeout, temperature, fallback strategy
- [ ] Execution mode (confirm `mock`)
- [ ] Case expiry hours
