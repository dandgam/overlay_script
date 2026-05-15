"""3-layer safety (spec §9).

1. PreToolUse hooks — deny dangerous combos
2. Deterministic interceptors — budget hard-cap, branch isolation, liveness
3. Branch isolation — worktree mechanic + git_merge tool validation
"""
