"""Per-worktree Anthropic API token bucket (spec §3 cap #7, handoff §5.2).

Без этого 3-5 parallel workers легко hit TPM/RPM лимиты, получают 429-е.
"""

from __future__ import annotations

# TODO: implement token bucket (capacity, refill_rate) per worktree key.
# Cache hit tokens НЕ считаются в ITPM — учитывать только non-cached input + output.
