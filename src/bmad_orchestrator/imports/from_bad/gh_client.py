"""GitHub client — gh CLI с curl fallback. Адаптировано из BAD.

Original: https://github.com/stephenleo/bmad-autonomous-development/blob/main/skills/bad/references/coordinator/pattern-gh-curl-fallback.md
License: MIT — Marie Stephen Leo

Что делает:
- Сначала пробует `gh <command>` (преферабельно — нативная auth)
- При неудаче (gh не установлен / no auth) → fallback на raw HTTPS через curl
  с заголовком `Authorization: Bearer $GITHUB_PERSONAL_ACCESS_TOKEN`

Зачем нам:
- Sandboxed environments часто не имеют gh
- Container-based workers могут не иметь gh auth pre-configured
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any


def gh_or_curl(
    gh_cmd: list[str],
    curl_url: str,
    method: str = "GET",
    body: dict[str, Any] | None = None,
) -> tuple[int, str]:
    """Try gh first, fallback to curl. Returns (exit_code, stdout)."""
    if shutil.which("gh"):
        try:
            result = subprocess.run(
                ["gh", *gh_cmd],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if result.returncode == 0:
                return 0, result.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    token = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        return 1, "GitHub token not available in env"

    curl_args = [
        "curl",
        "-sS",
        "-X",
        method,
        "-H",
        f"Authorization: Bearer {token}",
        "-H",
        "Accept: application/vnd.github+json",
        curl_url,
    ]
    if body:
        import json

        curl_args += ["-d", json.dumps(body)]

    result = subprocess.run(curl_args, capture_output=True, text=True, timeout=30, check=False)
    return result.returncode, result.stdout
