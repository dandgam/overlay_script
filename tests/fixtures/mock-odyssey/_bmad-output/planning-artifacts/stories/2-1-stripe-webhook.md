# Story 2-1-stripe-webhook

- **epic:** 2
- **status:** backlog
- **risk:** medium
- **estimated_tokens:** 45000
- **estimated_minutes:** 25
- **touches_files:**
  - src/billing/stripe_webhook.py
  - tests/test_stripe_webhook.py
- **touches_shared:** []
- **depends_on:** []
- **security_critical:** true
- **requires_human:** false

## Description
Accept Stripe payment_intent.succeeded webhook, verify signature, mark invoice paid.

## Acceptance
- POST /billing/stripe webhook verifies HMAC sig
- invoice.paid_at populated
- Unknown event types return 202 (no-op)
