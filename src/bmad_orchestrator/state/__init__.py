"""Persistent state (SQLite via aiosqlite). См. spec §6 (Where things live → state.db).

Single-writer guarantee — все мутации идут через async functions в db.py.
"""

from bmad_orchestrator.state.db import (
    SCHEMA_SQL,
    StateDB,
    connect,
    init_db,
)

__all__ = ["SCHEMA_SQL", "StateDB", "connect", "init_db"]
