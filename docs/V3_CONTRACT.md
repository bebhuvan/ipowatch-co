# IPO Watch V3 Contract

Root: `data/ipo_watch_v3/`  
Compatibility alias: `data/site_v3 -> data/ipo_watch_v3`  
Schema version: `3.0.0`  
Currency: INR  
Money unit: integer paise  
Percent unit: integer basis points  
Dates: ISO 8601  
Timezone: Asia/Kolkata  
Subscription multiple: decimal string

## Layout

```txt
data/ipo_watch_v3/
  manifest.json
  _meta/
    contract.json
    build_report.json
    source_coverage.json
    validation_report.json
    v2_comparison.json
    schemas/
  issues/
    index.json
    by-slug/<slug>.json
    by-year/*.json
    by-status/*.json
    by-kind/*.json
    quarantine/*.json
    <slug>/
      core.json
      market.json
      subscription.json
      filings.json
      prospectus_facts.json
      provenance.json
  companies/
    index.json
    by-slug/<slug>.json
  trajectories/
    <slug>.json
  analytics/
    performance.json
    cohorts.json
    source_quality.json
```

## Issue Modules

- `issues/by-slug/<slug>.json`: compatibility summary plus links to component files.
- `core.json`: identity, timeline, pricing, parties, book-building, quality state.
- `market.json`: listing/current prices, returns, CAGR when available, market source quality.
- `subscription.json`: demand/subscription state.
- `filings.json`: RHP/DRHP/prospectus/document metadata.
- `prospectus_facts.json`: citation-verified prospectus facts only; otherwise redacted.
- `provenance.json`: sources, field provenance, and validation findings.

## Public Index Rules

Public indexes include clean and review-tier records only. Quarantined records are retained under `issues/quarantine/` for audit but are not included in `issues/index.json`, status indexes, kind indexes, year indexes, companies, or analytics.

## Refresh Classes

Daily full refresh:

- Command: `.venv/bin/python -m ipo_portal.orchestrator refresh-daily`
- Rebuilds source snapshots, normalized staging data, validation reports,
  source coverage, manifest, analytics, and the deterministic V3 export.
- Existing verified/review `prospectus_facts.json` files are preserved across
  export unless they are failed or placeholder outputs.

High-frequency subscription refresh:

- Command: `.venv/bin/python -m ipo_portal.orchestrator refresh-subscriptions`
- Intended for active/open/upcoming issues only.
- Public writes are limited to `issues/<slug>/subscription.json`,
  `trajectories/<slug>.json`, `issues/by-slug/<slug>.json` subscription fields,
  and matching summary rows in public indexes/company issue lists.
- `trajectories/<slug>.json` stores category subscription observations under
  `observations[]` and NSE price-level demand snapshots under
  `demand_curves[]`.
- Consolidated all-exchange subscription books are published under
  `issues/<slug>/subscription.json` at `subscription.consolidated` when BSE
  consolidated bid-detail endpoints provide them. Per-exchange books remain
  under `subscription.by_exchange`.

Filing intelligence batch:

- Command: `.venv/bin/python -m ipo_portal.orchestrator process-filings`
- Writes only citation-verified, redacted-safe facts to
  `issues/<slug>/prospectus_facts.json`.
- Model calls are cached and cost logged under `data/reports/`.

Primary source-structure audit:

- Command: `.venv/bin/python -m ipo_portal.orchestrator audit-source-structure`
- Report: `data/reports/primary_source_structure_audit.json`
- Intended gate before declaring all NSE/BSE/SEBI primary-market surfaces
  complete.

## Schemas

Authoritative schemas live under `docs/schema/v3/` and are copied into `data/ipo_watch_v3/_meta/schemas/` during export.
