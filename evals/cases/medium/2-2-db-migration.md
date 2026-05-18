# Story 2.2: Database migration — add `users.last_seen_at`

**Epic:** epic-2
**Status:** ready-for-dev
**Level:** medium

## Acceptance Criteria

1. Migration `0042_add_last_seen.sql` adds `last_seen_at TIMESTAMPTZ` to `users` table
2. NULL allowed (backfill in next migration, not this one)
3. Index `idx_users_last_seen_at` created (filtered, NOT NULL)
4. Down migration drops both column + index
5. Migration tested via dry-run on test DB

## Tasks

- [ ] AC1+3: Write SQL migration `migrations/0042_*.sql`
- [ ] AC2: NULL semantics explicit in column definition
- [ ] AC4: Down migration in same file (DOWN section)
- [ ] AC5: Migration test in `tests/test_migrations.py`

## File List

- `migrations/0042_add_last_seen.sql`
- `tests/test_migrations.py`

## Dev Notes

Schema change + reversibility. Test must run the migration up-then-down.
