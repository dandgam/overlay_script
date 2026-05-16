# Story 1-3-tenant-disable

- **epic:** 1
- **status:** backlog
- **risk:** high
- **estimated_tokens:** 70000
- **estimated_minutes:** 40
- **touches_files:**
  - src/tenant/disable.py
  - tests/test_disable.py
- **touches_shared:**
  - src/tenant/models.py
- **depends_on:**
  - 1-1-tenant-signup
- **security_critical:** true
- **requires_human:** true

## Description
Admin endpoint to disable a tenant (soft delete). Security-critical: writes audit log.

## Acceptance
- POST /admin/tenant/{id}/disable returns 204
- audit_log row inserted with actor + reason
- Only admin role passes auth check
