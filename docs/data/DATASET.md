# IPO Watch — canonical Indian-IPO dataset

This is the public-facing documentation for the v2 dataset that powers
[IPO Watch](https://ipo-watch.local). It is intended to be the
**canonical machine-readable source** for Indian IPO data — every
mainboard and SME IPO, OFS, rights issue, buyback, QIP, IPP, InvIT,
REIT, ZCZP, NCB, SGB, takeover and delisting offer covered by the
National Stock Exchange (NSE) and Bombay Stock Exchange (BSE), enriched
with SEBI capital-raising statistics and prospectus-derived fields.

## Audience

* **Site readers** — viewing IPO data on ipo-watch.local.
* **Downstream consumers** — partner sites, finance dashboards, LLM
  agents, analysts ingesting the JSON tree.
* **Contributors** — extending the schema, adding sources, debugging
  contamination.

If you are an automated consumer, start with
[`SCHEMA_GUIDE.md`](SCHEMA_GUIDE.md) for field semantics and the JSON
Schemas under `docs/schema/v2/`. The schema URL is in every record's
`$schema` field; validate before ingesting.

## Coverage

* **Geographies**: India (Mainboard + SME).
* **Time range**: From whatever year NSE/BSE expose, currently 2017
  onwards for performance data, full history for issue metadata.
* **Issue kinds**: `ipo_mainboard`, `ipo_sme`, `ofs`, `rights`,
  `buyback`, `qip`, `ipp`, `invit`, `reit`, `zczp`, `ncb`, `sgb`,
  `takeover_open_offer`, `voluntary_delisting`, plus SEBI aggregates.
* **Daily updates**: Hourly for active subscriptions, daily for
  document indexes, monthly for performance backfills.

Refresh cadence and staleness tolerance for each endpoint is in
[`SOURCES.md`](SOURCES.md). The dataset manifest at
`data/site_v2/manifest.json` carries the actual freshness state of each
source for the build.

## Conventions

| Concept | Storage | Display |
|---------|---------|---------|
| Money | Integer **paise** (₹ × 100) in fields named `<concept>_paise`. | Computed display field `<concept>_inr_text` (e.g., `"₹15.00 Cr"`). |
| Subscription times | `Decimal(10,4)` in fields named `<concept>_x`. | `<concept>_text` (e.g., `"7.57x"`). |
| Percentages | Integer **basis points** in `<concept>_bps`. | `<concept>_pct_text`. |
| Date-only | ISO 8601 `YYYY-MM-DD`. | Locale-formatted at render time. |
| Instants | ISO 8601 with explicit UTC offset. | IST clock-time in `<field>_ist` when needed. |
| Time zone | All timestamps stored as UTC. Inputs without offset are treated as `Asia/Kolkata`. | |
| Currency | `INR` everywhere (no FX conversion). | |
| Language | `en-IN` for descriptive strings. Original Unicode preserved (NFC). | |
| Identifiers | Slug = `<normalized-name>-<6-char-id>`. ISIN preferred when present. | |

These conventions are normative. The full rationale is in
[`FUTURE_PROOFING.md`](FUTURE_PROOFING.md) §8.

## What's in every record

Every JSON document (every issue, every company, every aggregate) is
wrapped in the metadata envelope defined in
`ipo_portal/orchestrator/metadata.py`. Read it once; the contract is
binding for the whole dataset.

Required keys (in this order at the top of every file):

1. `$schema` — URL to the JSON Schema. Always present.
2. `schema_version` — SemVer of the schema. Always present.
3. `schema_url_self` — Stable URL where this document lives.
4. `dataset` — Dataset short-name (e.g., `ipo-watch.issues`).
5. `dataset_version` — Build-time version (date-stamped).
6. `generated_at` — ISO 8601 UTC of build time.
7. `generated_by` — Tool that produced the document.
8. `time_zone`, `currency`, `language` — Convention markers.
9. `sources[]` — Every upstream source that contributed.
10. `field_provenance{}` — For multi-source fields, which source won.
11. `data_quality` — `{state, errors[], warnings[]}`.
12. `freshness{}` — Last successful refresh per source.
13. `license` — Usage license.

Then the schema-specific body.

## Quality tiers

Each record's `data_quality.state` is one of:

* `clean` — passed all validation rules.
* `review` — at least one `error`-tier validation finding.
  Publishable but needs human attention.
* `quarantined` — at least one `blocking`-tier finding. **Not
  published** in the public indexes; lives under
  `data/site_v2/quarantine/` for triage.

If you are a downstream consumer, the typical filter is
`state in ('clean', 'review')`. The `errors[]` and `warnings[]` arrays
on each record list the specific rules that fired (rule IDs are stable
forever — see [`EDGE_CASES.md`](EDGE_CASES.md)).

## File layout

```
data/site_v2/
├── manifest.json                  # dataset-level state and counts
├── issues/
│   ├── index.json                 # flat list of every issue
│   ├── by-slug/<slug>.json        # full per-issue record
│   ├── by-year/<YYYY>.json        # year rollups
│   ├── by-status/<state>.json     # upcoming, active, closed, listed, …
│   └── by-kind/<kind>.json        # ipo_mainboard, ofs, rights, …
├── companies/
│   ├── index.json
│   └── by-slug/<slug>.json
├── trajectories/
│   └── <issue_slug>.json          # hourly subscription series
├── quarantine/
│   └── <slug>.json                # blocking-quality records
└── audit/
    └── <slug>/                    # per-record change history
```

## License

See the `license` field in each record. Default policy is
**CC-BY-4.0 with attribution to IPO Watch**, but underlying exchange
filings retain their original copyright. Verify against original
filings before making investment decisions.

## Versioning

* Dataset version (`dataset_version`) increments on every build,
  date-stamped.
* Schema version (`schema_version`) follows SemVer. MAJOR changes go to
  a new tree (`data/site_v3/`).
* The schema changelog is at [`CHANGELOG.md`](CHANGELOG.md).

## See also

* [`SCHEMA_GUIDE.md`](SCHEMA_GUIDE.md) — every field, semantics,
  examples.
* [`SOURCES.md`](SOURCES.md) — every upstream endpoint we scrape.
* [`EDGE_CASES.md`](EDGE_CASES.md) — every contamination pattern we
  defend against, with stable rule IDs.
* [`FUTURE_PROOFING.md`](FUTURE_PROOFING.md) — schema evolution
  policy, validation tiers, contracts.
* [`CHANGELOG.md`](CHANGELOG.md) — schema change history.
