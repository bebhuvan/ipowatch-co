"""DeepSeek-driven orchestrator for the v2 IPO database rebuild.

Subcommands (run via ``python -m ipo_portal.orchestrator <subcommand>``):

* ``catalog``     — Phase 1: per-endpoint field catalogue from raw snapshots.
* ``schema``      — Phase 2: synthesize canonical v2 JSON Schemas + Python models.
* ``audit``       — Phase 3: pollution / collision audit on existing site data.
* ``normalize``   — Phase 4: build data/site_v2/ using v2 schema + dedup rules.
* ``gap-scan``    — Phase 5: surface missing first-class fields against catalog.
* ``enrich-rhp``  — Phase 6: extract structured prospectus data from RHP PDFs.

DeepSeek is the analyst. Deterministic Python is the executor. Every output
file carries the metadata envelope defined in ``orchestrator.metadata`` so
external consumers can read a single JSON in isolation and know what it is,
where it came from, when it was generated, and which schema validates it.
"""

from __future__ import annotations

__version__ = "0.1.0"
PIPELINE_NAME = "ipo_portal.orchestrator"
SCHEMA_BASE_URL = "https://ipo-watch.local/schema/v2"
