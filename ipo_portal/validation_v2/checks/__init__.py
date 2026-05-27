"""V2 validation checks.

Each submodule registers rules covering one category from
``docs/data/EDGE_CASES.md``. Importing this package registers all rules
with the engine's default registry. ``checks.ids`` is the starter set;
the rest are added as the v2 schema is locked in Phase 2 and the
normalizer is built in Phase 4.
"""

from __future__ import annotations

from . import core  # noqa: F401  — registers core rules

__all__ = ["core"]
