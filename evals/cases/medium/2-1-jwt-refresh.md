# Story 2.1: JWT refresh token rotation

**Epic:** epic-2
**Status:** ready-for-dev
**Level:** medium

## Acceptance Criteria

1. POST `/auth/refresh` accepts a valid refresh token, returns new access+refresh pair
2. Old refresh token is revoked (single-use)
3. If old token reused → 401 + family revocation (all sibling tokens invalidated)
4. Refresh token expires 30 days from issue
5. Tests cover: happy path, reuse detection, expiration

## Tasks

- [ ] AC1+2: Implement rotation in `auth/refresh.py`
- [ ] AC3: Family-revocation logic when reuse detected
- [ ] AC4: Token TTL enforcement
- [ ] AC5: 4 unit tests in `tests/test_refresh.py`

## File List

- `src/auth/refresh.py`
- `src/auth/tokens.py` (token family tracking)
- `tests/test_refresh.py`

## Dev Notes

Multi-file, security-critical. Worker should fit single pass if specs followed.
Compliance tags: none (test fixture — no real PII).
