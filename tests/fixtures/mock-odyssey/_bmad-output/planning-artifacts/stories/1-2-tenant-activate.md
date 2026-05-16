# Story 1-2-tenant-activate

- **epic:** 1
- **status:** backlog
- **risk:** medium
- **estimated_tokens:** 55000
- **estimated_minutes:** 30
- **touches_files:**
  - src/tenant/activate.py
  - tests/test_activate.py
- **touches_shared:**
  - src/tenant/models.py
- **depends_on:**
  - 1-1-tenant-signup
- **security_critical:** false
- **requires_human:** false

## Description
Email-verification activation flow for newly signed-up tenants.

## Acceptance
- POST /tenant/activate?token=… flips `tenants.is_active = true`
- Token TTL 24h, expired returns 410
- ruff + mypy + pytest pass
