# AI Revenue Recovery MVP — Build Runbook

## Direct answer

For the **first hackathon MVP**, you do not need to train a sophisticated custom model before building the system. You can build and demonstrate the complete recovery loop with:

1. deterministic payment-failure rules;
2. a small, transparent diagnosis classifier if desired;
3. a policy and economic engine;
4. a bounded LLM decision step;
5. mock payment and messaging adapters;
6. a simulated batch with treatment and holdout groups;
7. an audit trail and revenue dashboard.

The model is not the first dependency. The first dependency is a reliable event-to-outcome pipeline.

> **Recommended order:** build the pipeline and safety controls first, add a baseline model second, and add model optimization only after the system can measure recovery correctly.

## What you must do manually

Claude Code can write much of the implementation, but you must provide and approve the business decisions and external configuration.

| Manual responsibility | Why you must do it |
|---|---|
| Choose the MVP scope | The system must begin with failed subscriptions only. |
| Define the allowed actions | Only you or the business owner can approve which actions are permitted. |
| Set retry limits and contact caps | These are policy decisions, not coding decisions. |
| Define cooldown and escalation rules | These affect customer treatment and risk. |
| Provide approved message templates | The system must not invent risky payment claims or pressure language. |
| Decide what counts as recovery | Observed payment and incremental recovery must be distinguished. |
| Provide or approve sample event data | Claude can generate fixtures, but you must verify that they represent the intended business flow. |
| Configure API keys and sandbox credentials | Never ask Claude to invent or expose credentials. |
| Verify payment-provider schemas | The exact webhook payload and signature behavior must be confirmed from the provider documentation. |
| Review model assumptions | Synthetic data can demonstrate mechanics but cannot prove production performance. |
| Approve production access | Real money and customer contact require a human release decision. |

## Do you need model training?

### Minimum hackathon path: no custom training required

For the first working demo, use a deterministic diagnosis map:

```text
insufficient_funds     → delayed retry or approved reminder
expired_card           → alternate payment method or human escalation
issuer_decline         → approved reminder or escalation
network_error          → retry after bounded delay
unknown                → human review or stop
```

This is sufficient to demonstrate the complete system architecture. The intelligence is still meaningful because the system combines event detection, diagnosis, policy reasoning, bounded language generation, execution, measurement, and auditability.

### Stronger MVP path: train one small baseline classifier

If you want a visible ML component, train one transparent classifier for root-cause classification or retry-success prediction. Use a simple model such as logistic regression, decision tree, or gradient-boosted trees.

You need labeled rows such as:

```text
payment failure features
→ failure category
→ whether a later retry succeeded
```

Claude Code can create the training script and evaluation tests. You must validate:

- which fields are available before the action;
- whether labels are correct;
- whether the features leak the future outcome;
- whether a random split would make performance look artificially good;
- whether the probabilities are calibrated;
- whether there is a deterministic fallback for low confidence.

### Production path: training is required later

A production system should eventually train and monitor models using real historical events, outcomes, and customer-interaction data. It would need temporal evaluation, calibration, drift monitoring, segment analysis, and retraining governance.

Do not block the hackathon MVP on this. First prove that the pipeline can safely execute and measure a recovery episode.

## Do you need to build the ingestion pipeline manually?

You do not need to manually write every line of ingestion code. Claude Code can implement the webhook handler, canonical schema, database tables, deduplication, validation, and tests.

You must manually provide or confirm:

1. the source event types;
2. the exact payload fields;
3. the event signature or authentication method;
4. the customer and subscription identifiers;
5. the amount and currency representation;
6. the payment-failure codes;
7. the outcome events that confirm payment success or failure;
8. the sandbox endpoint and credentials;
9. the behavior for duplicate and out-of-order events.

For the first demo, avoid a real provider if it slows you down. Use a local event simulator that emits fixed JSON fixtures. Later, replace the simulator with a sandbox adapter without changing the core domain contracts.

## What to do after each step

### Step 1 — Freeze the MVP decisions

**You do:** Decide that version one supports failed subscription payments only, one simulated communication channel, a mock gateway, and a fixed batch of test cases.

**Give Claude Code:** The revised architecture document and the following instruction:

```text
Read @docs/revised-architecture.md.
The MVP is failed-subscription recovery only.
Do not add checkout abandonment, B2B receivables, voice, WhatsApp,
real-money execution, or multi-agent behavior.
Before editing, list the project assumptions that require human approval.
```

**Your completion check:** You can state the exact events, actions, metrics, and stop rules in one page.

### Step 2 — Create the repository and technical plan

**Claude Code does:** Inspect the repository, propose the folder structure, identify dependencies, and create a plan without editing first. Claude Code officially supports planning before edits and staged test-driven workflows.[1]

**You do:** Review the plan. Reject unnecessary frameworks and features.

**Your approval condition:** The plan contains explicit contracts for events, cases, actions, outcomes, ledger entries, and audit events.

### Step 3 — Build domain contracts and the state machine

**Claude Code does:** Implement typed schemas, database tables, state transitions, and tests.

**You do:** Review allowed and forbidden transitions.

**Run these checks:**

```text
A recovered case cannot receive another retry.
A disputed case cannot receive automated outreach.
An opted-out case cannot receive a message.
A duplicate event cannot create a duplicate action.
A holdout case cannot receive treatment outreach.
```

**Do not continue until:** All state-machine and idempotency tests pass.

### Step 4 — Define the policy configuration manually

**You do:** Fill in the policy file. Start with explicit demo values and label them as demo configuration.

Example:

```yaml
policy_version: policy_demo_v1
max_payment_retries_per_episode: 3
max_customer_contacts_per_week: 2
cooldown_hours_between_contacts: 24
high_value_approval_threshold_minor: 100000
minimum_expected_net_recovery_minor: 500
stop_on_opt_out: true
stop_on_dispute: true
escalate_on_unknown_decline: true
```

**Claude Code does:** Implement the policy engine, candidate generation, expected-value scoring, and table-driven tests.

**You do:** Verify every threshold and confirm that the LLM cannot change them.

**Do not continue until:** The policy engine can explain why every candidate is allowed, blocked, or below the economic threshold.

### Step 5 — Create and validate the event fixtures

**Claude Code does:** Generate a local event simulator and fixtures for:

```text
transient failure
insufficient funds
expired card
unknown decline
successful retry
maximum-contact stop
opt-out stop
dispute escalation
duplicate event
out-of-order event
```

**You do:** Inspect the fixtures and correct unrealistic assumptions. Confirm that each event has a stable customer ID, subscription ID, event ID, amount, currency, timestamp, and outcome relationship.

**Do not continue until:** One command can replay the complete batch from input events to final outcomes.

### Step 6 — Decide whether to add training

Choose one of the following paths:

| Path | When to choose it | Action |
|---|---|---|
| Rules only | You need the fastest credible demo | Implement decline-code rules and show reason codes. |
| One baseline model | You want a visible ML component | Train one transparent classifier using labeled fixtures and evaluate it. |
| Advanced optimization | You already have trustworthy historical data | Defer until after the MVP; do not use reinforcement learning first. |

If using synthetic data, describe the model as a **demonstration baseline**. Do not claim that it predicts real customers accurately.

**Your role:** Approve the feature list, label definition, data split, and acceptance threshold.

**Claude Code's role:** Write the training script, evaluation report, model serialization, inference adapter, and tests.

### Step 7 — Implement diagnosis

**Claude Code does:** Implement the rule/model adapter with this output:

```json
{
  "root_cause": "insufficient_funds",
  "success_probability_now": 0.18,
  "recommended_retry_window": "24_to_72_hours",
  "reason_codes": ["DECLINE_CODE_MATCH"],
  "confidence": 0.84,
  "model_version": "diagnosis_demo_v1"
}
```

**You do:** Confirm that all outputs are persisted, versioned, and explainable. Check that the exact retry delay is still selected by policy candidates, not by the model or LLM.

### Step 8 — Implement candidate generation and economic scoring

**Claude Code does:** Generate fixed candidates such as:

```text
retry_after_24_hours
retry_after_72_hours
send_approved_email_template_01
offer_approved_alternate_method
escalate_to_human
stop_pursuit
```

Then calculate:

```text
expected_net_recovery
= calibrated_success_probability × recoverable_amount
  − action_cost
  − operational_cost
  − risk_penalty
```

**You do:** Approve the scoring formula, thresholds, and treatment of uncertain predictions.

**Do not continue until:** The LLM is unable to create a candidate or modify candidate parameters.

### Step 9 — Add the bounded LLM

**Claude Code does:** Implement a structured-output call that accepts candidate IDs and returns exactly one selected candidate plus approved message variables.

**You do:** Review the system prompt, allowed fields, templates, and refusal behavior.

Test at least:

```text
malformed JSON
unknown candidate ID
changed retry amount
changed retry delay
attempt to ignore opt-out
attempt to override a dispute
customer message containing prompt injection
model timeout
```

**Do not continue until:** Every invalid response is rejected and routed to deterministic fallback or human review.

### Step 10 — Implement mock execution

**Claude Code does:** Build idempotent payment, messaging, and human-escalation adapters.

**You do:** Keep all execution simulated. Confirm that repeated requests with the same idempotency key do not repeat the action.

**Run these tests:**

```text
same action submitted twice
success webhook arrives twice
failure webhook arrives after a timeout
success arrives after a retry was scheduled
case is stopped while an action is pending
```

### Step 11 — Implement treatment/holdout measurement

**Claude Code does:** Assign a stable cohort, prevent treatment actions for holdout cases, reconcile outcomes, and calculate ledger metrics.

**You do:** Verify that:

- cohort assignment is persistent;
- holdout cases are not accidentally contacted;
- recovery attribution has a defined time window;
- refunds and cancellations are handled;
- one payment cannot create two ledger entries;
- observed recovery is separated from estimated incremental recovery.

### Step 12 — Build the audit trail and dashboard

**Claude Code does:** Build the case timeline and dashboard.

**You do:** Select the batch scenarios and verify every case manually from event to final result.

The dashboard must show:

```text
total at-risk ₹
observed treatment recovered ₹
observed holdout recovered ₹
estimated incremental recovered ₹
recovery lift
average time to recovery
cost to recover ₹1
stopped cases
human escalations
invalid LLM responses
execution failures
```

### Step 13 — Conduct the final engineering review

Ask Claude Code to review without modifying files:

```text
Review the complete implementation against @docs/revised-architecture.md.
Do not edit files.
Find release-blocking issues involving:
- money movement;
- policy bypass;
- duplicate execution;
- incorrect revenue attribution;
- prompt injection;
- PII or secret exposure;
- missing audit records;
- invalid state transitions;
- model leakage;
- unsafe defaults.
Classify findings as blocker, high, medium, or low.
```

**You or an AI engineer must:** Review and resolve every blocker and high-severity finding before presenting the demo.

## What not to do

Do not begin by training a large model. Do not connect real payment APIs before idempotency and policy tests pass. Do not let the LLM call arbitrary payment or messaging tools. Do not build all six revenue-loss workflows at once. Do not claim synthetic post-message payments as proven AI recovery. Do not put secrets in prompts, source code, fixtures, or `CLAUDE.md`.

## Practical responsibility split

| Area | You | Claude Code | AI engineer |
|---|---|---|---|
| MVP scope | Own | Implement | Review |
| Business policy | Own | Encode and test | Approve risk boundaries |
| Event schema | Confirm provider meaning | Implement | Validate integration correctness |
| Ingestion | Provide source details | Code and test | Review reliability |
| Diagnosis model | Approve labels/features | Train baseline | Validate leakage and calibration |
| LLM integration | Approve templates and behavior | Implement | Red-team boundaries |
| Payment execution | Keep sandbox-only | Implement mock adapter | Approve production readiness |
| Revenue attribution | Define business meaning | Code ledger | Validate experiment design |
| Dashboard | Choose demo story | Build UI | Validate metric integrity |
| Production release | Approve business readiness | Prepare deployment | Sign off safety and operations |

## Recommended starting point

Your immediate next action should be:

1. create the repository;
2. place the revised architecture in `docs/revised-architecture.md`;
3. create `CLAUDE.md` with the non-negotiable boundaries;
4. start Claude Code in plan mode;
5. ask it to produce the implementation plan only;
6. review and approve the plan;
7. build contracts and tests before training any model.

### References

[1]: https://code.claude.com/docs/en/common-workflows "Claude Code — Common workflows"
[2]: https://code.claude.com/docs/en/permissions "Claude Code — Configure permissions"
[3]: https://code.claude.com/docs/en/permission-modes "Claude Code — Choose a permission mode"
