# Story 1-1-tenant-signup

- **epic:** 1
- **status:** ready-for-dev
- **risk:** low
- **estimated_tokens:** 1000
- **estimated_minutes:** 20
- **touches_files:**
  - src/tenant/signup.py
  - tests/test_signup.py
- **touches_shared:** []
- **depends_on:** []
- **security_critical:** false
- **requires_human:** false

## Description
Create tenant signup endpoint that accepts email + password and writes to `tenants` table.

## Acceptance
- POST /tenant/signup returns 201 with tenant_id
- duplicate email returns 409
- ruff + mypy + pytest pass
