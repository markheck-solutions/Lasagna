"""Public API for the read-only INCA evidence collector."""

# ruff: noqa: F401,F403,I001
from __future__ import annotations

from . import inca_evidence_collector_artifacts as _artifacts
from . import inca_evidence_collector_context as _context
from . import inca_evidence_collector_fileio as _fileio
from . import inca_evidence_collector_live as _live
from . import inca_evidence_collector_phases as _phases
from . import inca_evidence_collector_predicate_scan as _predicate_scan
from . import inca_evidence_collector_predicate_sql as _predicate_sql
from . import inca_evidence_collector_probe_snapshots as _probe_snapshots
from . import inca_evidence_collector_semantic_fetch as _semantic_fetch
from . import inca_evidence_collector_semantic_results as _semantic_results
from . import inca_evidence_collector_setup as _setup
from . import inca_evidence_collector_state as _state
from .inca_evidence_collector_setup import main

_PART_MODULES = (
    _context,
    _setup,
    _phases,
    _semantic_fetch,
    _semantic_results,
    _probe_snapshots,
    _state,
    _fileio,
    _live,
    _predicate_scan,
    _predicate_sql,
    _artifacts,
)

_PUBLIC_NAMES: dict[str, object] = {}
for _module in _PART_MODULES:
    for _name, _value in vars(_module).items():
        if _name.startswith("_") and _name not in {"__version__"}:
            continue
        _PUBLIC_NAMES[_name] = _value

for _module in _PART_MODULES:
    _module.__dict__.update(_PUBLIC_NAMES)

globals().update(_PUBLIC_NAMES)
__all__ = sorted(_PUBLIC_NAMES)
