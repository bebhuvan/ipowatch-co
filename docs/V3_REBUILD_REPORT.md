# IPO Watch V3 Rebuild Report

Build date: 2026-05-24  
Dataset root: `data/ipo_watch_v3/`  
Compatibility alias: `data/site_v3 -> data/ipo_watch_v3`  
Dataset version: `v3.2026.05.24-0653`

## What Was Fetched Fresh

V3 was exported raw-first from the latest snapshots under `data/raw/`, then materialized into the public contract. The raw inventory contains NSE, BSE, SEBI-derived parser inputs where available, Kite/Yahoo performance snapshots, and comparison-only aggregators.

Current V3 output:

- Public issues: 5,196
- Companies: 4,355
- Subscription trajectories: 214
- Performance analytics rows: 2,486
- Quarantined/removed from public indexes: 1,891

Source coverage is explicit in `data/ipo_watch_v3/_meta/source_coverage.json`:

- Total classified endpoints: 21,317
- Parsed into V3 canonical records: 20,921
- Parsed as document metadata only: 4
- Intentionally ignored helper/dropdown feeds: 8
- Unsupported fallback/aggregator gaps: 301
- Parser failed: 83
- Fetch failed: 0
- Unclassified: 0

## What Failed

No latest raw snapshot in this build had a failed HTTP status. Parser gaps and unsupported feeds are listed explicitly in `source_coverage.json`; no endpoint is silently unclassified.

## DeepSeek Extraction

V3 emits `prospectus_facts.json` for every public issue. Unverified facts remain
redacted with `extraction_status` set to `not_extracted` or
`no_prospectus_document`.

Three real filing-intelligence outputs are currently preserved across rebuilds:

- `a-g-universal-c13e20`: pass.
- `a-b-infrabuild-c81c90`: pass.
- `20-microns-nano-minerals-f0cd70`: review.

`export-v3` reattaches existing extracted `prospectus_facts.json` files when
they are model-produced and quality state is `pass` or `review`. Failed or
placeholder documents are not preserved as published facts.

## Quarantine Policy

V3 public indexes exclude quarantined records. The validation report lists every finding class and samples in `data/ipo_watch_v3/_meta/validation_report.json`.

Quarantine/review rules cover name-only records, missing identity, invalid ISINs, impossible prices, impossible date order, impossible market returns, stale active statuses, no-date/no-document thin records, and unverified prospectus facts.

## Why V3 Is Better Than V2

V2 published 7,087 issue records and 5,139 companies. V3 publishes 5,196 issue records and 4,355 companies after stricter quarantine and de-duplication, with 3,189 clean records and 2,007 review-tier records.

Concrete improvements:

- Public tree is self-contained under `data/ipo_watch_v3/`.
- Issue records are split into `core`, `market`, `subscription`, `filings`, `prospectus_facts`, and `provenance`.
- Source coverage is endpoint-level and has no unclassified endpoint.
- Market returns are recomputed from canonical paise prices.
- Public indexes do not include quarantined records.
- V2 comparison is machine-readable in `_meta/v2_comparison.json`.

## Still Needs Work

- Implement verified DeepSeek prospectus extraction for recent/current filings.
- Close the 83 parser-failed endpoint classifications.
- Decide whether unsupported aggregator feeds should be retired or formally modeled as comparison-only inputs.
- Add deeper candle-based drawdown analytics where Yahoo/Kite candles are available.

## Site Consumption

The Astro site reads `data/ipo_watch_v3` through `web/src/lib/ipodata.ts`.
`web/src/lib/issues.ts` adapts canonical units into display units for pages.
Issue detail pages render verified prospectus sections from
`issues/<slug>/prospectus_facts.json`. There is no runtime dependency on
`data/site_v2`.

## Refresh Paths

Daily full refresh:

```bash
.venv/bin/python -m ipo_portal.orchestrator refresh-daily
```

Subscription-only refresh for active issues:

```bash
.venv/bin/python -m ipo_portal.orchestrator refresh-subscriptions
```

The subscription refresh is designed for Git-hosted data: it can fetch/normalize
fresh raw snapshots, but public V3 writes are limited to active issue
subscription modules, matching public index summary fields, and trajectories.

Optional filing intelligence batch:

```bash
.venv/bin/python -m ipo_portal.orchestrator process-filings --limit 10 --quality-gate
```

Scheduled workflows:

- `.github/workflows/refresh-daily.yml`
- `.github/workflows/refresh-subscriptions.yml`

## Reproduce

```bash
.venv/bin/python -m ipo_portal.orchestrator export-v3
.venv/bin/python scripts/audit_v3_quality.py --site-root data/ipo_watch_v3 --gate
.venv/bin/python scripts/audit_prospectus_facts.py --site-root data/ipo_watch_v3 --gate
python3 -m pytest -q --ignore=tests/test_kite_auth.py
cd web && npm run build
```

Determinism check for this run: two consecutive exports produced the same SHA-256 tree hash:

`266d7bf7b68ec32466cca355a89aae74700f10d2fed54637f8b21cfff235c7f4`
