# Demo Script — Case-by-Case Walkthrough

**Authority:** `CLAUDE.md` and `docs/revised-architecture.md`
**Scope:** Failed-subscription recovery MVP
**Data:** All cases use synthetic fixtures. Results are labeled as "estimated from simulated batch."

> This document describes the expected walkthrough for each demo case. The demo runs as a single batch command (`python run_batch.py`) and produces a dashboard with metrics, case timelines, and audit trails.

---

## Pre-Demo Setup

1. All policy values in `docs/policy-decisions.md` have been approved and written to `policies/policy_demo_v1.yaml`.
2. All message templates have been approved and stored in `templates/`.
3. The database is initialized with empty tables.
4. Execution mode is `mock` — no real payments or messages.
5. LLM adapter is `mock` by default (optionally switch to a real provider for live demo).

**Command:** `python run_batch.py`

**Expected output:** Processing log for each case, followed by dashboard metrics summary.

---

## Demo Case 1: Successful Delayed Retry (Transient Gateway Failure)

**Fixture:** Customer Aarav, subscription ₹999/month, decline code `network_error`.

### Walkthrough

| Step | State | What happens |
|---|---|---|
| 1. Event ingested | `RECEIVED` | A `subscription.payment_failed` event with `decline_code: network_error` is received. Raw event persisted. |
| 2. Normalized | `NORMALIZED` | Event validated, case created with `customer_id`, `subscription_id`, `amount_minor: 99900`, `currency: INR`. |
| 3. Eligibility checked | `ELIGIBILITY_CHECKED` | Customer has not opted out, no dispute, within retry and contact caps, cooldown clear. Result: `allowed = true`. |
| 4. Diagnosed | `DIAGNOSED` | Rule engine classifies as transient network error. `root_cause: network_error`, `success_probability_now: high`, `recommended_retry_window: immediate_to_24_hours`. |
| 5. Candidates generated | `CANDIDATES_GENERATED` | Policy generates candidates: `retry_after_24_hours`, `send_approved_email_template_01`, `escalate_to_human`, `stop_pursuit`. |
| 6. Scored | `ACTION_SCORED` | Economic engine scores each candidate. `retry_after_24_hours` has highest expected net recovery due to high transient-retry probability. |
| 7. LLM selects | `DECISION_PENDING` → `VALIDATED` | LLM selects `retry_after_24_hours`. Validator confirms: candidate exists, parameters unchanged, no policy violation. |
| 8. Executed | `ACTION_SCHEDULED` → `ACTION_EXECUTED` | Mock payment adapter retries with idempotency key. |
| 9. Outcome received | `AWAITING_OUTCOME` | Mock gateway returns `success`. |
| 10. Reconciled | `RECOVERED` | Case marked recovered. Ledger entry created: `amount_recovered_minor: 99900`, `attribution_status: observed_after_action`, `cohort: treatment`. |

### What to show the audience

- The complete audit timeline from event to recovery.
- The candidate set with economic scores.
- The LLM selection matching the highest-scored candidate.
- The ledger entry.

---

## Demo Case 2: Message-Assisted Recovery (Insufficient Funds)

**Fixture:** Customer Priya, subscription ₹1,499/month, decline code `insufficient_funds`.

### Walkthrough

| Step | State | What happens |
|---|---|---|
| 1. Event ingested | `RECEIVED` → `NORMALIZED` | Payment failed with `insufficient_funds`. |
| 2. Eligibility checked | `ELIGIBILITY_CHECKED` | All checks pass. |
| 3. Diagnosed | `DIAGNOSED` | `root_cause: insufficient_funds`, `success_probability_now: low`, `recommended_retry_window: 24_to_72_hours`. |
| 4. Candidates generated | `CANDIDATES_GENERATED` | `retry_after_24_hours`, `retry_after_72_hours`, `send_approved_email_template_01`, `escalate_to_human`, `stop_pursuit`. |
| 5. Scored | `ACTION_SCORED` | Email reminder has reasonable expected net recovery. Immediate retry is scored lower due to low probability. |
| 6. LLM selects | `VALIDATED` | LLM selects `send_approved_email_template_01`. Personalizes: `customer_first_name: "Priya"`, `payment_amount_display: "₹1,499"`, `support_link: "https://example.test/support"`. |
| 7. Executed | `ACTION_EXECUTED` | Mock messaging adapter sends the email. Delivery status: `delivered`. |
| 8. Outcome | `AWAITING_OUTCOME` | Customer pays after receiving the email (simulated via outcome fixture). |
| 9. Reconciled | `RECOVERED` | Ledger entry: `observed_after_action`, within attribution window. |

### What to show the audience

- The LLM selecting a messaging candidate over a retry (with reasoning).
- The approved template with personalization variables — only allowed variables used.
- The distinction between "observed after action" and "caused by action."

---

## Demo Case 3: Expired Payment Method Escalation

**Fixture:** Customer Vikram, subscription ₹2,999/month, decline code `expired_card`.

### Walkthrough

| Step | State | What happens |
|---|---|---|
| 1. Event ingested | `RECEIVED` → `NORMALIZED` | Payment failed with `expired_card`. |
| 2. Eligibility checked | `ELIGIBILITY_CHECKED` | All checks pass. |
| 3. Diagnosed | `DIAGNOSED` | `root_cause: expired_card`, `success_probability_now: very low` (retry with same card will fail). |
| 4. Candidates generated | `CANDIDATES_GENERATED` | `offer_approved_alternate_method`, `escalate_to_human`, `stop_pursuit`. Retry candidates are excluded or scored near zero because the card is expired. |
| 5. Scored | `ACTION_SCORED` | `escalate_to_human` or `offer_approved_alternate_method` is the best option. |
| 6. LLM selects | `VALIDATED` | LLM selects `escalate_to_human` (or alternate method, depending on scoring). |
| 7. Executed | `ESCALATED_TO_HUMAN` | Mock escalation adapter creates a review ticket with full case context. |

### What to show the audience

- The diagnosis correctly identifying that retrying with an expired card is futile.
- The system escalating rather than blindly retrying.
- The human review ticket with complete context.

---

## Demo Case 4: Maximum-Contact Stop

**Fixture:** Customer Meera, subscription ₹499/month, decline code `insufficient_funds`. She has already received `max_customer_contacts_per_week` contacts this week.

### Walkthrough

| Step | State | What happens |
|---|---|---|
| 1. Event ingested | `RECEIVED` → `NORMALIZED` | New payment failure. |
| 2. Eligibility checked | `ELIGIBILITY_CHECKED` → `STOPPED_BY_POLICY` | Contact cap reached. Eligibility returns `allowed = false`, reason: `CONTACT_CAP_REACHED`. |
| 3. Stopped | `STOPPED_BY_POLICY` (terminal) | No candidates generated. No LLM call. No action executed. |

### What to show the audience

- The policy engine stopping the case before any action.
- The reason code `CONTACT_CAP_REACHED` in the audit trail.
- The case counted in the "policy stop rate" metric.

---

## Demo Case 5: Opt-Out Stop

**Fixture:** Customer Ravi, subscription ₹799/month, `opted_out: true`.

### Walkthrough

| Step | State | What happens |
|---|---|---|
| 1. Event ingested | `RECEIVED` → `NORMALIZED` | Payment failed. |
| 2. Eligibility checked | `NORMALIZED` → `CUSTOMER_OPTED_OUT` | Customer opted out. Eligibility returns `allowed = false`, reason: `CUSTOMER_OPTED_OUT`. |
| 3. Stopped | `CUSTOMER_OPTED_OUT` (terminal) | No candidates. No LLM. No outreach. No retry. |

### What to show the audience

- Immediate, unconditional stop on opt-out — no exceptions.
- The safety invariant enforced in code, not just in prompts.
- The audit event proving the stop.

---

## Demo Case 6: Dispute Escalation

**Fixture:** Customer Anita, subscription ₹1,999/month, `disputed: true`.

### Walkthrough

| Step | State | What happens |
|---|---|---|
| 1. Event ingested | `RECEIVED` → `NORMALIZED` | Payment failed. |
| 2. Eligibility checked | `NORMALIZED` → `CUSTOMER_DISPUTED` | Active dispute detected. Eligibility returns `allowed = false`, reason: `CUSTOMER_DISPUTED`. |
| 3. Stopped | `CUSTOMER_DISPUTED` (terminal) | No automated recovery action. |

### What to show the audience

- Dispute stop is immediate and unconditional.
- The case is clearly separated from recoverable cases in the dashboard.
- No revenue from this case is attributed to recovery.

---

## Demo Case 7: Malformed LLM Response and Fallback

**Fixture:** Customer Deepak, subscription ₹1,499/month, decline code `issuer_decline`. The mock LLM adapter is configured to return malformed output.

### Walkthrough

| Step | State | What happens |
|---|---|---|
| 1–5. Normal flow | `RECEIVED` → … → `ACTION_SCORED` | Case proceeds normally through diagnosis and scoring. |
| 6. LLM called | `DECISION_PENDING` | Mock LLM returns invalid JSON: `"I think you should retry the payment for ₹0"`. |
| 7. Validation fails | — | Validator rejects: malformed JSON. `LLM_RESPONSE_REJECTED` audit event appended. |
| 8. Fallback used | `VALIDATED` | Deterministic fallback selects the highest-scored eligible candidate. `FALLBACK_USED` audit event appended. |
| 9. Executed | `ACTION_EXECUTED` → `AWAITING_OUTCOME` | Fallback candidate is executed safely. |
| 10. Outcome | Depends on fixture | Case concludes normally. |

### What to show the audience

- The raw LLM response in the audit trail (visibly invalid).
- The validator catching it.
- The deterministic fallback producing a safe result.
- The case completing successfully despite LLM failure.
- This case counted in the "invalid LLM response rate" metric.

---

## Demo Case 8: Duplicate Gateway Event

**Fixture:** Customer Kavya, subscription ₹999/month. The same `event_id` is submitted twice in the batch.

### Walkthrough

| Step | State | What happens |
|---|---|---|
| 1. First event | `RECEIVED` → `NORMALIZED` | Case created normally. |
| 2. Duplicate event | — | Deduplication store recognizes the `event_id`. No new case created. `EVENT_DEDUPLICATED` audit event appended. |
| 3. Processing continues | Normal flow | Only one case exists. Only one set of actions is generated. |

### What to show the audience

- Two events in the input, one case in the output.
- The deduplication audit event.
- No duplicate actions, no duplicate ledger entries.

---

## Demo Case 9: Holdout Customer Who Later Pays

**Fixture:** Customer Suresh, subscription ₹1,499/month, decline code `insufficient_funds`. Cohort assignment: `holdout` (hash falls in 80–99 range).

### Walkthrough

| Step | State | What happens |
|---|---|---|
| 1. Event ingested | `RECEIVED` → `NORMALIZED` | Payment failed. |
| 2. Cohort assigned | — | `stable_hash(customer_id + case_id) % 100` = 85 → `holdout`. Assignment persisted. |
| 3. Eligibility checked | `ELIGIBILITY_CHECKED` | All safety checks pass. |
| 4. Holdout enforcement | `STOPPED_BY_POLICY` | No treatment outreach is executed. Case is recorded as holdout. |
| 5. Later payment | — | A payment success event arrives (the customer paid on their own). |
| 6. Reconciled | `RECOVERED` | Case marked recovered. Ledger entry: `cohort: holdout`, `attribution_status: holdout_recovery`. |

### What to show the audience

- The holdout case receives no outreach — proving the control group is clean.
- The natural payment is recorded but **not** attributed to AI.
- The holdout recovery rate is used to estimate the treatment lift.
- This is the foundation of honest incremental revenue measurement.

---

## Post-Batch Dashboard Walkthrough

After all 9 cases process, present the dashboard with these metrics:

| Metric | Source cases | Expected observation |
|---|---|---|
| **Total at-risk revenue** | All 9 cases | Sum of all `principal_amount_minor` |
| **Observed treatment recovery rate** | Cases 1, 2 (and any other treatment recoveries) | Recovered treatment / total treatment |
| **Observed holdout recovery rate** | Case 9 | Recovered holdout / total holdout |
| **Estimated recovery lift** | All | Treatment rate − holdout rate |
| **Estimated incremental recovered ₹** | All | Lift × at-risk × treatment fraction |
| **Total observed recovered ₹** | Cases 1, 2, 9 | Sum of recovered amounts |
| **Average time to recovery** | Recovered cases | Mean time from case creation to recovery |
| **Cost to recover ₹1** | Treatment cases | Total action costs / treatment recovered |
| **Policy stop rate** | Cases 4, 5, 6 | Stopped / total |
| **Human escalation rate** | Case 3 | Escalated / total |
| **Invalid LLM response rate** | Case 7 | Rejected / total LLM calls |
| **Execution failure rate** | None expected | 0 / total executions |

### Key talking points

1. **"The system recovers revenue while respecting customer rights."** Cases 5 and 6 prove that opt-outs and disputes stop the system immediately.

2. **"The AI is bounded — it selects from approved options, it doesn't invent them."** Case 7 proves that invalid AI output is caught and the system falls back safely.

3. **"We measure incrementally, not by claiming every payment."** Case 9 proves that natural recovery is separated from AI-influenced recovery.

4. **"Every decision is auditable."** Any case can be expanded to show the complete timeline from raw event to final state.

5. **"The system is safe by default."** Duplicate events (Case 8) don't create duplicate actions. Policy caps (Case 4) stop pursuit automatically. The economic engine (all cases) prevents unprofitable actions.

---

## Audience Q&A Preparation

| Likely question | Answer path |
|---|---|
| "How do you know the AI actually helped?" | Show the treatment vs. holdout comparison. Point to Case 9 as the control. |
| "What if the AI goes rogue?" | Show Case 7: invalid output is rejected, fallback is used, the audit trail records everything. |
| "What about customer complaints?" | Show Cases 5 and 6: opt-outs and disputes stop the system immediately. Show Case 4: contact caps prevent over-messaging. |
| "Is this using real money?" | No. All adapters are mocks. Show the `execution_mode: mock` configuration. |
| "How accurate is the model?" | The MVP uses deterministic rules, not a trained model. The probabilities are demo estimates, clearly labeled. |
| "Can you scale this?" | The architecture separates concerns into five planes. Each can be scaled independently. But today's demo proves the safety and measurement loop first. |
| "What's the business case?" | Point to estimated incremental recovered ₹ and cost to recover ₹1. Note these are estimates from a simulated batch — real numbers require real data. |
