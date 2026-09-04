"""Approved WhatsApp message templates for the Revenue Recovery MVP.

Templates are the only message type available outside a customer-service
window (Meta reference [2] in docs/whatsapp-followup-addon.md). Every
template here is from the §"Templates to create for the MVP" table.

The LLM may personalize the surrounding explanation but must not
generate URLs, change amounts, or add variables beyond the allowed set.
"""

from __future__ import annotations

from .models import ApprovedTemplate

# ---------------------------------------------------------------------------
# Template registry
# ---------------------------------------------------------------------------

_APPROVED_TEMPLATES: dict[str, ApprovedTemplate] = {}


def _register(t: ApprovedTemplate) -> ApprovedTemplate:
    _APPROVED_TEMPLATES[t.template_name] = t
    return t


# 1. Explain the failure and next approved path
PAYMENT_FAILED_EXPLANATION = _register(
    ApprovedTemplate(
        template_name="payment_failed_explanation_v1",
        language="en",
        allowed_variables=("first_name", "amount", "payment_link"),
        body_text=(
            "Hi {{first_name}}, your subscription payment of {{amount}} could not be "
            "completed because your payment method needs attention. You can update it "
            "securely here: {{payment_link}}. Reply HELP if you need assistance, or "
            "STOP to opt out of messages."
        ),
        category="UTILITY",
    )
)

# 2. Ask the customer to update the payment method
PAYMENT_METHOD_UPDATE = _register(
    ApprovedTemplate(
        template_name="payment_method_update_v1",
        language="en",
        allowed_variables=("first_name", "payment_link"),
        body_text=(
            "Hi {{first_name}}, you can update your payment method securely here: "
            "{{payment_link}}. If you need help, reply HELP."
        ),
        category="UTILITY",
    )
)

# 3. Confirm that a human review has been requested
HUMAN_SUPPORT_FOLLOWUP = _register(
    ApprovedTemplate(
        template_name="human_support_followup_v1",
        language="en",
        allowed_variables=("first_name", "case_reference"),
        body_text=(
            "Hi {{first_name}}, we've received your request for assistance. "
            "A support specialist will review your case (ref: {{case_reference}}) "
            "and reach out to you shortly."
        ),
        category="UTILITY",
    )
)

# 4. Confirm only after verified reconciliation
PAYMENT_RECOVERED_CONFIRMATION = _register(
    ApprovedTemplate(
        template_name="payment_recovered_confirmation_v1",
        language="en",
        allowed_variables=("first_name", "amount"),
        body_text=(
            "Hi {{first_name}}, your payment of {{amount}} has been successfully "
            "processed. Thank you for resolving this."
        ),
        category="UTILITY",
    )
)

# 5. Confirm that automated follow-ups have stopped
OPT_OUT_CONFIRMATION = _register(
    ApprovedTemplate(
        template_name="opt_out_confirmation_v1",
        language="en",
        allowed_variables=("first_name",),
        body_text=(
            "Hi {{first_name}}, you have been opted out of automated messages. "
            "You will not receive further follow-ups from us."
        ),
        category="UTILITY",
    )
)

# Additional templates for response candidates
RESOLUTION_EXPLANATION_RESPONSE = _register(
    ApprovedTemplate(
        template_name="resolution_explanation_response_v1",
        language="en",
        allowed_variables=("first_name", "payment_link"),
        body_text=(
            "We can solve this by updating your payment method through the secure "
            "link: {{payment_link}}. After the method is updated, the system will "
            "check the subscription status and use only the retry action allowed by "
            "the recovery policy. We will not retry if the payment is disputed, "
            "cancelled, or outside the approved rules."
        ),
        category="UTILITY",
    )
)

ALTERNATIVE_OPTIONS_RESPONSE = _register(
    ApprovedTemplate(
        template_name="alternative_options_response_v1",
        language="en",
        allowed_variables=("first_name", "payment_link"),
        body_text=(
            "Hi {{first_name}}, here are the available options:\n"
            "1. Update your payment method: {{payment_link}}\n"
            "2. Reply HELP to speak with a support specialist.\n"
            "We can only offer the options approved by the recovery policy."
        ),
        category="UTILITY",
    )
)

PROMISE_TO_PAY_ACKNOWLEDGMENT = _register(
    ApprovedTemplate(
        template_name="promise_to_pay_acknowledgment_v1",
        language="en",
        allowed_variables=("first_name", "promised_date"),
        body_text=(
            "Hi {{first_name}}, we've noted that you intend to pay by "
            "{{promised_date}}. We'll check back then. If you need help, "
            "reply HELP."
        ),
        category="UTILITY",
    )
)

ALREADY_PAID_ACKNOWLEDGMENT = _register(
    ApprovedTemplate(
        template_name="already_paid_acknowledgment_v1",
        language="en",
        allowed_variables=("first_name",),
        body_text=(
            "Hi {{first_name}}, thank you for letting us know. We are "
            "verifying your payment. No further action is needed from you "
            "at this time."
        ),
        category="UTILITY",
    )
)

DISPUTE_ACKNOWLEDGMENT = _register(
    ApprovedTemplate(
        template_name="dispute_acknowledgment_v1",
        language="en",
        allowed_variables=("first_name", "case_reference"),
        body_text=(
            "Hi {{first_name}}, we have recorded your dispute "
            "(ref: {{case_reference}}). All automated recovery actions have "
            "been stopped. A specialist will review your case."
        ),
        category="UTILITY",
    )
)

PROMPT_INJECTION_RESPONSE = _register(
    ApprovedTemplate(
        template_name="safe_fallback_response_v1",
        language="en",
        allowed_variables=("first_name",),
        body_text=(
            "Hi {{first_name}}, I can only help with your payment-related "
            "question. Reply HELP to speak with a support specialist, or "
            "STOP to opt out of messages."
        ),
        category="UTILITY",
    )
)

PAYMENT_LINK_RESPONSE = _register(
    ApprovedTemplate(
        template_name="payment_link_response_v1",
        language="en",
        allowed_variables=("first_name", "amount", "payment_link"),
        body_text=(
            "Hi {{first_name}}, here is your secure payment link for "
            "{{amount}}: {{payment_link}}. This link will expire in 24 hours."
        ),
        category="UTILITY",
    )
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_template(template_name: str) -> ApprovedTemplate | None:
    """Look up a template by name. Returns None if not approved."""
    return _APPROVED_TEMPLATES.get(template_name)


def list_templates() -> list[str]:
    """Return all approved template names."""
    return list(_APPROVED_TEMPLATES.keys())


def validate_template(
    template_name: str, variables: dict[str, str]
) -> tuple[bool, list[str]]:
    """Validate that a template exists and variables are allowed.

    Returns (is_valid, list_of_error_strings).
    """
    errors: list[str] = []

    template = get_template(template_name)
    if template is None:
        errors.append(f"template '{template_name}' is not an approved template")
        return False, errors

    provided_keys = set(variables.keys())
    allowed_keys = set(template.allowed_variables)

    extra = provided_keys - allowed_keys
    if extra:
        errors.append(
            f"variables {sorted(extra)} are not allowed for template '{template_name}'; "
            f"allowed: {sorted(allowed_keys)}"
        )

    missing = allowed_keys - provided_keys
    if missing:
        errors.append(
            f"required variables {sorted(missing)} are missing for template '{template_name}'"
        )

    return len(errors) == 0, errors


def render_template(template_name: str, variables: dict[str, str]) -> str | None:
    """Render a template with the provided variables.

    Returns None if template not found or validation fails.
    Only allowed variables are substituted; unknown placeholders
    remain as-is (defense in depth).
    """
    template = get_template(template_name)
    if template is None:
        return None

    is_valid, _ = validate_template(template_name, variables)
    if not is_valid:
        return None

    body = template.body_text
    for var_name in template.allowed_variables:
        placeholder = "{{" + var_name + "}}"
        value = variables.get(var_name, placeholder)
        body = body.replace(placeholder, value)

    return body
