"""CLI package — typer + rich (spec §14).

Public entry point: `bmad_orchestrator.cli:app` (см. pyproject.toml [project.scripts]).
Импплементация разложена по модулям:

- `main`        — typer commands per §14.1
- `tui`         — rich.Live dashboard per §14.2
- `i18n`        — load locale/*.yaml + simple ``t(key, **kwargs)`` lookup
- `models_yaml` — load/save ``orchestrator-models.yaml`` per §16.4
"""

from __future__ import annotations

from bmad_orchestrator.cli.main import app

__all__ = ["app"]
