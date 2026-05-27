# Schema changelog

Every notable change to the v2 schema is recorded here. Format is
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) adapted for a
data schema. Versions follow SemVer:

* **MAJOR** — backward-incompatible (new tree `data/site_v3/`).
* **MINOR** — new fields, new rules, new sources.
* **PATCH** — descriptions, examples, fixes that don't change shape.

## [Unreleased]

The v2 schema is in initial development. The first cut will be tagged
as `2.0.0` when Phase 2 (canonical schema design) finalizes.

### Added (in-progress)
- v2 metadata envelope contract (`$schema`, sources, provenance,
  freshness, license) — see `docs/data/DATASET.md`.
- `ipo_portal/orchestrator/` with `catalog` and `drift` subcommands.
- `ipo_portal/normalization/units.py` for paise / Decimal / IST /
  Indian-format parsing.
- `ipo_portal/validation_v2/` with 4-tier severity engine and stable
  `rule_id`s matching `docs/data/EDGE_CASES.md`.
- `docs/data/EDGE_CASES.md` — 30+ contamination patterns documented
  with rule IDs (`E.ID.*`, `E.CUR.*`, `E.DAT.*`, `E.NUM.*`, `E.SUB.*`,
  `E.SRC.*`, `E.STA.*`, `E.HTM.*`, `E.SNP.*`, `E.PAG.*`, `E.UPS.*`,
  `E.LEG.*`).
- `docs/data/FUTURE_PROOFING.md` — binding policy.
- `docs/schema/raw_catalog/` — per-endpoint Phase 1 catalogs with
  contamination risks, refresh cadence, staleness tolerance, PII flags.

### Changed
- Existing `data/site/` continues to ship unchanged during the v2
  build; cutover is planned after Phase 4 verifies normalizer
  parity with the live site.
- **2026-05-23 (post-Phase-4):**
  - `identity.status` enum gained `Filed` (offer doc on record, outcome
    unconfirmed). Status is now inferred from the timeline when no source
    reports it.
  - `identity.issue_type` enum gained `NCD`, `SGB`, `InvIT`, `REIT`
    (debt IPOs, sovereign gold bonds, trusts) — previously bucketed as
    `Others`.
  - Section `required` arrays relaxed: the structural JSON Schema now
    validates types/enums/no-unknown-fields, while *completeness* is the
    rule engine's concern. Only `identity.slug` + `identity.company_name`
    are structurally required.
  - Date parser (`E.DAT`) now rejects implausible years (outside
    `[1990, current+2]`) and repairs corrupt years from a sibling
    anchor date (e.g., `"0202-02-07"` → `"2022-02-07"`).
  - Records are now consolidated across join keys (ISIN / name+symbol /
    document-only absorption) so an issue split across sources merges to
    one canonical record.

### Added (2026-05-23, post-Phase-4)
- SEBI public-issues filing scraper (`ipo_portal/sebi.py`) — earliest
  DRHP signal; parser `sebi_filings`.
- 12 v2 parsers covering NSE (current/upcoming/past/OFS/rights/tender/
  IPP/InvIT/REIT/offer-docs), BSE (public-issue/performance), Moneycontrol,
  SEBI. ~4k canonical issue records.
- Aggregation indexes: `issues/index.json`, `by-year/`, `by-status/`,
  `by-kind/`, `companies/index.json`, `companies/by-slug/`.
- JSON Schema enforcement gate (`schema_check.py`) — structural
  validation against `issue.schema.json` before publish.
- Subscription trajectory v2 (`trajectory_v2.py`) — bid-book time-series
  per active issue, mapped to canonical slugs.
- `refresh` orchestration command (hot / full cycles) + `refresh_runs.jsonl`.
- Operator + consumer docs: `docs/OPERATIONS.md`, `docs/CONSUMER_GUIDE.md`,
  `docs/REFRESH_CYCLE.md`.

### Deprecated
- (nothing yet)

### Removed
- (nothing — see `FUTURE_PROOFING.md` §1.2: never remove a field, only
  deprecate.)

### Security
- DeepSeek API key (used by the orchestrator only) is stored in `.env`
  (gitignored) as `DEEPSEEK_API_KEY`. The orchestrator never persists
  the key to data files.
