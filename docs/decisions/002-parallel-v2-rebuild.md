# 002 — Parallel v2 rebuild via DeepSeek orchestration

**Status:** Accepted
**Date:** 2026-05-23
**Authors:** Bhuvanesh R + IPO Watch contributors

## Context

The existing v1 pipeline (`ipo_portal/normalize.py`, `site_builder.py`)
produces `data/site/` with 11,662 issues and 8,285 companies. It works
but has known problems:

* 873 source-identifier collision warnings (mostly company-name
  variants).
* NSE/BSE dual-listed mainboard IPOs are not deduped on the upcoming
  page.
* Many first-class fields (anchor allocation, lead managers, lot size,
  prospectus risk factors) are not surfaced.
* Currency / date / number conventions are inconsistent across
  source-specific normalizers.
* Validation is a single binary clean/quarantined dimension — no
  severity tiers.
* No metadata envelope, no field provenance, no documentation contract
  that lets an LLM agent ingest a single record in isolation.

We want IPO Watch to be the **canonical** machine-readable source for
Indian IPO data. That means the schema, validation, and documentation
all have to be redesigned together.

## Decision

Build a **parallel v2 pipeline** that writes to `data/site_v2/` rather
than refactoring v1 in place. The v1 pipeline keeps shipping until the
v2 normalizer reaches parity and is verified.

Use DeepSeek as the **analytical orchestrator** for tasks that benefit
from natural-language reasoning over messy data:

* Phase 1 (`catalog`) — DeepSeek catalogues each raw endpoint's fields,
  semantics, contamination risks.
* Phase 2 (`schema`) — DeepSeek synthesizes canonical JSON Schemas
  from the aggregated catalogue.
* Phase 3 (`audit`) — DeepSeek inspects current `data/site/` records
  to identify contamination patterns and propose dedup rules.
* Phase 6 (`enrich-rhp`) — DeepSeek extracts structured prospectus
  fields from RHP / DRHP PDFs.

Phases 4 (`normalize`) and 5 (`gap-scan`) are deterministic Python and
do **not** call DeepSeek — they consume the schemas and rules produced
by the analytical phases.

All DeepSeek output is disk-cached by SHA256 of the request so reruns
cost nothing.

## Alternatives considered

1. **In-place hardening of v1.** Fix the 873 collisions, dedup
   NSE/BSE on `/upcoming/`, add per-type aggregations. Rejected: lower
   risk but lower ceiling — the schema-level problems
   (units, provenance, validation severity, documentation contract)
   require a schema break.
2. **Full re-scrape + new schema.** Delete `data/raw/`, refetch every
   source. Rejected: burns hours of bandwidth, risks NSE/BSE rate
   limiting, and the existing raw snapshots are still valid input for
   the v2 normalizer.
3. **Heavy LLM-in-the-loop runtime extraction.** Have DeepSeek
   re-extract structured fields from every snapshot on every build.
   Rejected: 11,000+ records × N sources is expensive, slow, and
   non-deterministic. Catalogue-once-then-parse-deterministically is
   strictly better.

## Consequences

* Two parallel trees (`data/site/` and `data/site_v2/`) until cutover.
  Disk cheap; complexity manageable because v2 is additive.
* New code under `ipo_portal/orchestrator/`, `ipo_portal/normalization/`,
  `ipo_portal/normalize_v2/`, `ipo_portal/validation_v2/`. v1 modules
  untouched.
* DeepSeek key in `.env` (gitignored). Cost telemetry in
  `data/reports/deepseek_usage.jsonl` so spend is auditable.
* Schema evolution policy (`FUTURE_PROOFING.md` §1) governs how the v2
  schema changes once tagged 2.0.0.
* Cutover task — once `data/site_v2/` is verified at parity:
  1. Astro site reads from `data/site_v2/`.
  2. `data/site/` becomes read-only and is eventually retired.
  3. `data/site_v2/` is renamed to `data/site/` (or the v2 path is kept
     to make the version explicit). Decision deferred to a future ADR.

## Related

* `docs/data/EDGE_CASES.md` — every contamination pattern this rebuild
  defends against.
* `docs/data/FUTURE_PROOFING.md` — the binding policy.
* `ipo_portal/orchestrator/` — Phase implementations.
* Memory: `project_v2_rebuild`, `feedback_future_proof`.
