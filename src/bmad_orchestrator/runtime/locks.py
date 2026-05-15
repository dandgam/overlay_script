"""flock(2) wrappers for shared files (spec §6.3).

Critical files:
- sprint-status.yaml
- deferred-work.md
"""

from __future__ import annotations

import fcntl
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def flock(path: Path, exclusive: bool = True) -> Generator[None, None, None]:
    """Acquire an exclusive (write) or shared (read) lock on a file.

    Usage:
        with flock(Path("sprint-status.yaml")):
            data = yaml.safe_load(open(...))
            data["1.2"] = "done"
            yaml.safe_dump(data, open(..., "w"))
    """
    flag = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    path.touch(exist_ok=True)
    with path.open("r+b") as fh:
        fcntl.flock(fh.fileno(), flag)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
