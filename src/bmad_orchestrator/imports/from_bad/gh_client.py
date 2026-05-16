"""GitHub client — gh CLI с urllib fallback. Адаптировано из BAD.

Original: https://github.com/stephenleo/bmad-autonomous-development/blob/main/skills/bad/references/coordinator/pattern-gh-curl-fallback.md
License: MIT — Marie Stephen Leo

Что делает:
- Сначала пробует `gh <command>` (преферабельно — нативная auth)
- При неудаче (gh не установлен / no auth) → fallback на raw HTTPS через
  stdlib `urllib.request` (token в headers, НЕ в argv — H5 fix vs curl `-H`)

Зачем нам:
- Sandboxed environments часто не имеют gh
- Container-based workers могут не иметь gh auth pre-configured
- urllib не светит token в `ps`/argv unlike `curl -H "Authorization: Bearer ..."`
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from typing import Any


def gh_or_curl(
    gh_cmd: list[str],
    curl_url: str,
    method: str = "GET",
    body: dict[str, Any] | None = None,
) -> tuple[int, str]:
    """Try gh first, fallback to urllib (H5 — no token in argv).

    Function name retained for back-compat; the actual fallback no longer
    invokes curl. Returns (exit_code_like, body_text). 0 on success, HTTP
    status code on HTTP error, 1 on transport error.
    """
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

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "bmad-orchestrator/0.1",
    }
    data: bytes | None = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(
        url=curl_url,
        data=data,
        method=method.upper(),
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return 0, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body_text = str(exc)
        return exc.code, body_text
    except urllib.error.URLError as exc:
        return 1, str(exc.reason)
