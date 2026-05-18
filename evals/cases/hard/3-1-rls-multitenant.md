# Story 3.1: Row-Level Security for multi-tenant orders table

**Epic:** epic-3
**Status:** ready-for-dev
**Level:** hard

## Acceptance Criteria

1. `orders` table gets RLS enabled with `tenant_id` policy
2. Materialized views over `orders` use `WITH (security_invoker = on)` (PG15+)
3. App role can SELECT only rows where `tenant_id = current_setting('app.tenant_id')::uuid`
4. Bypass attempts return 0 rows (not error — silent isolation)
5. Tests prove cross-tenant queries return empty (3 tenants × 3 cross-checks = 9 assertions)
6. Migration is reversible (DOWN drops policy + disables RLS)

## Tasks

- [ ] AC1+2: SQL migration with RLS policy + MV security_invoker
- [ ] AC3+4: GRANT/REVOKE setup for app role
- [ ] AC5: pytest-pg fixture creating 3 tenants, asserting isolation
- [ ] AC6: DOWN migration

## File List

- `migrations/0043_orders_rls.sql`
- `tests/test_orders_rls.py`

## Dev Notes

Security-critical, multi-tenant. Compliance tags: ["152-ФЗ"] — touches PII through orders.
Likely to require 2 iterations (worker tends to miss MV security_invoker on first pass).
**Expected outcome:** escalation OR ≥2 iterations — this case stresses P5 iteration cap.
