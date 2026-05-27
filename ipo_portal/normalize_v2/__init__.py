"""V2 normalization pipeline.

Builds ``data/site_v2/`` from ``data/raw/`` using:

* The canonical v2 schemas at ``docs/schema/v2/`` (Phase 2 output).
* Source precedence rules at ``docs/data/SOURCE_PRECEDENCE.yaml``.
* Dedup rules at ``docs/data/DEDUP_RULES.yaml`` (Phase 3 output).
* The normalization helpers in ``ipo_portal.normalization``.
* The validation engine in ``ipo_portal.validation_v2``.

This module **does not** touch ``data/site/`` — v1 keeps running until
cutover. The orchestrator's ``normalize`` subcommand drives it.
"""

from __future__ import annotations

from .precedence import PrecedenceRules, load_precedence
from .pipeline import V2Pipeline, run_normalize

__all__ = [
    "PrecedenceRules",
    "load_precedence",
    "V2Pipeline",
    "run_normalize",
]
