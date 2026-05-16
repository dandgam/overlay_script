#!/usr/bin/env python3
"""auto-promote-tracker.py — Recover from claude -p zombie pattern.

Pattern: claude -p does the session work + commits feat — but hangs before
writing tracker promotion (Current → Completed, next Pending → Current).
Watchdog kills the zombie ~50min later, then the next wake spends another
~25min on tracker bookkeeping. Total burn: ~45min/zombie.

This script does the bookkeeping in-process (no LLM), eliminating the burn.

Usage:
  auto-promote-tracker.py <tracker_path> <integration_branch>

Behavior:
  1. Parse `### Current` block — extract id (e.g. S18) + title + surface
  2. git log <branch> --grep "feat(...): <id> —" — extract feat commit hash
  3. git status — must be clean (no uncommitted changes)
  4. If all 3 conditions met (zombie pattern):
     - Build minimal Completed entry: id/title/completed_at/surface/commit
     - Move first Pending block → Current (or (none — done) if Pending empty)
     - Prepend Completed entry to ### Completed
     - Append journal entry
     - Write tracker via bash .claude/scripts/write-claude-file.sh
     - git commit "tracker(<slug>): <id> auto-promoted (zombie recovery)"
     - Print summary; exit 0
  5. Otherwise (not a zombie or current is still working):
     - Print reason; exit 1 (tells launcher to proceed normally)

Idempotent: running twice is a no-op (after first call, Current changes).
Safe: never touches Pending blocks beyond moving the first to Current.
Read-only on git history; only adds new commits.
"""

import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def section_range(text: str, header: str) -> tuple[int, int] | None:
    """Find (start_line_idx, end_line_idx) of section bounded by `### header`."""
    lines = text.split("\n")
    start = None
    for i, line in enumerate(lines):
        if line.strip() == header:
            start = i
            break
    if start is None:
        return None
    # End at next ### or ## header
    for i in range(start + 1, len(lines)):
        line = lines[i]
        if line.startswith("### ") or line.startswith("## "):
            return (start, i)
    return (start, len(lines))


def section_body(text: str, header: str) -> str:
    """Get section body text (excluding the header line itself)."""
    rng = section_range(text, header)
    if not rng:
        return ""
    lines = text.split("\n")
    return "\n".join(lines[rng[0] + 1 : rng[1]])


def replace_section(text: str, header: str, new_body: str) -> str:
    """Replace section body. Preserves header and trailing blank lines structure."""
    rng = section_range(text, header)
    if not rng:
        return text
    lines = text.split("\n")
    new_lines = lines[: rng[0] + 1] + new_body.split("\n") + lines[rng[1] :]
    return "\n".join(new_lines)


def parse_session_block(body: str) -> dict | None:
    """Parse a block starting with `- **id:** Sxx`. Returns dict with key fields, or None if empty."""
    m = re.search(r"^- \*\*id:\*\*\s*(\S+)\s*$", body, re.M)
    if not m:
        return None
    sid = m.group(1)
    title = ""
    surface = ""
    tm = re.search(r"^\s+\*\*title:\*\*\s*(.+?)$", body, re.M)
    if tm:
        title = tm.group(1).strip()
    sm = re.search(r"^\s+\*\*surface:\*\*\s*(.+?)$", body, re.M)
    if sm:
        surface = sm.group(1).strip()
    return {"id": sid, "title": title, "surface": surface}


def split_session_blocks(body: str) -> list[str]:
    """Split a section body into list of session blocks (each starts with `- **id:**`)."""
    blocks: list[str] = []
    current: list[str] = []
    for line in body.split("\n"):
        if line.startswith("- **id:**"):
            if current:
                blocks.append("\n".join(current).rstrip())
                current = []
            current.append(line)
        elif current:
            current.append(line)
    if current:
        blocks.append("\n".join(current).rstrip())
    return blocks


def find_feat_commit(branch: str, sid: str) -> str | None:
    """git log <branch> --grep 'feat\\(...\\): <sid> —' — return short hash or None."""
    pat = re.compile(rf"^([0-9a-f]+) feat\([^)]+\): {re.escape(sid)} —")
    res = subprocess.run(
        ["git", "log", branch, "--pretty=format:%h %s", "-50"],
        capture_output=True,
        text=True,
    )
    for line in res.stdout.splitlines():
        m = pat.match(line)
        if m:
            return m.group(1)
    return None


def working_tree_clean() -> bool:
    res = subprocess.run(
        ["git", "status", "--porcelain"], capture_output=True, text=True
    )
    return res.stdout.strip() == ""


def slug_from_path(tracker_path: str) -> str:
    name = Path(tracker_path).stem
    if name.startswith("initiative-tracker-"):
        return name[len("initiative-tracker-") :]
    return name


def write_via_helper(tracker_path: str, content: str) -> None:
    """write-claude-file.sh bypasses the .claude/ Edit-block in headless mode."""
    helper = Path(".claude/scripts/write-claude-file.sh")
    if not helper.exists():
        Path(tracker_path).write_text(content)
        return
    res = subprocess.run(
        ["bash", str(helper), tracker_path],
        input=content,
        text=True,
        capture_output=True,
    )
    if res.returncode != 0:
        raise RuntimeError(f"helper failed: {res.stderr}")


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(
            "Usage: auto-promote-tracker.py <tracker_path> <integration_branch>",
            file=sys.stderr,
        )
        return 2
    tracker_path = argv[1]
    branch = argv[2]
    p = Path(tracker_path)
    if not p.exists():
        print(f"error: tracker not found: {tracker_path}", file=sys.stderr)
        return 2

    text = p.read_text()
    slug = slug_from_path(tracker_path)

    # 1. Parse Current
    cur_body = section_body(text, "### Current").strip("\n")
    cur_clean = cur_body.strip()
    if not cur_clean or "(none" in cur_clean.lower() or "(empty)" in cur_clean.lower():
        print("not a zombie: Current is empty/none — initiative likely done")
        return 1
    cur_session = parse_session_block(cur_body)
    if not cur_session:
        print("not a zombie: cannot parse Current block (no - **id:** found)")
        return 1
    sid = cur_session["id"]

    # 2. Check feat commit exists in branch
    feat_hash = find_feat_commit(branch, sid)
    if not feat_hash:
        print(
            f"not a zombie: no feat commit for {sid} on {branch} — claude still working"
        )
        return 1

    # 3. Working tree must be clean
    if not working_tree_clean():
        print("not a zombie: working tree dirty — claude still actively writing")
        return 1

    # 4. All zombie conditions met. Promote.
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Pending: extract first block
    pend_body = section_body(text, "### Pending")
    pend_blocks = split_session_blocks(pend_body)
    next_block = pend_blocks[0] if pend_blocks else None
    rest_pending = "\n\n".join(pend_blocks[1:]) if len(pend_blocks) > 1 else ""

    # Build new sections
    completed_entry = (
        f"- **id:** {sid}\n"
        f"  **title:** {cur_session['title']}\n"
        f"  **completed_at:** {ts}\n"
        f"  **surface:** {cur_session['surface']}\n"
        f"  **commit:** {feat_hash}\n"
        f"  **promotion:** auto-promote-tracker.py (zombie recovery)\n"
    )

    if next_block:
        new_current_body = "\n" + next_block + "\n"
    else:
        new_current_body = "\n(none — Pending empty after auto-promotion)\n"

    new_pending_body = (
        "\n"
        + (
            rest_pending
            if rest_pending
            else "(empty — all sessions promoted to Completed)"
        )
        + "\n"
    )

    # Existing Completed body (preserve)
    completed_body = section_body(text, "### Completed")
    new_completed_body = "\n" + completed_entry + "\n" + completed_body.lstrip("\n")

    # Apply replacements
    new_text = text
    new_text = replace_section(new_text, "### Pending", new_pending_body.rstrip("\n"))
    new_text = replace_section(new_text, "### Current", new_current_body.rstrip("\n"))
    new_text = replace_section(
        new_text, "### Completed", new_completed_body.rstrip("\n")
    )

    # Append journal entry
    journal_line = f"[{ts}] {sid} auto-promoted by auto-promote-tracker.py (feat hash {feat_hash}, zombie recovery)"
    new_text = re.sub(
        r"(^## Journal\s*\n(?:.*\n)*?)((?=^## ))",
        lambda m: m.group(1).rstrip() + "\n" + journal_line + "\n\n",
        new_text,
        count=1,
        flags=re.M,
    )

    # Write
    write_via_helper(tracker_path, new_text)

    # Commit
    next_id = parse_session_block(next_block)["id"] if next_block else "(none)"
    msg = (
        f"tracker({slug}): {sid} auto-promoted (zombie recovery), {next_id} → Current\n\n"
        f"Detected zombie pattern: feat commit {feat_hash} for {sid} present, "
        f"working tree clean, but tracker Current still = {sid}.\n"
        f"Bookkeeping done by auto-promote-tracker.py without invoking claude -p, "
        f"saving ~45min/incident.\n"
    )
    subprocess.run(["git", "add", tracker_path], check=True)
    subprocess.run(["git", "commit", "-m", msg], check=True)

    print(f"OK: {sid} → Completed (commit {feat_hash}); {next_id} → Current")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
