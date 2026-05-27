# Future-proofing policy

The v2 IPO database is intended to be **the canonical source** for Indian
IPO data on IPO Watch and any downstream consumer (LLM agents, partner
sites, analysts). It must survive years of upstream changes, schema
evolution, source additions and deprecations, regulatory shifts, and
contributor turnover without losing trustworthiness.

This document is the binding policy. Code reviews and pull requests
reference its sections. If the policy here conflicts with a design
choice in a PR, the policy wins or the policy must be amended explicitly
in the same PR.

Related documents:
* **`EDGE_CASES.md`** — the contamination patterns this policy defends
  against (rule IDs `E.<CATEGORY>.<NNN>`).
* **`SCHEMA_GUIDE.md`** — per-field semantics, units, examples.
* **`SOURCES.md`** — every upstream endpoint, refresh cadence,
  staleness tolerance.
* **`SOURCE_PRECEDENCE.yaml`** — declarative source-precedence rules.
* **`CHANGELOG.md`** — schema version history.

---

## 1. Schema evolution

### 1.1 Versioning
* Schemas live under `docs/schema/v2/` and are JSON Schema (draft 2020-12).
* Every schema has a SemVer `$id` URL ending with the major version
  (`https://ipo-watch.local/schema/v2/issue.schema.json`).
* MAJOR bumps go to a sibling tree (`docs/schema/v3/`) and a new
  `data/site_v3/` output. v2 keeps producing for the deprecation window.
* MINOR bumps add fields. PATCH bumps fix descriptions / examples.
* Every record carries `$schema` (URL) and `schema_version` (string).

### 1.2 Deprecation, never deletion
* A field is never removed from the v2 schema. To retire a field:
  1. Mark it `"deprecated": true` and add `"deprecated_since": "<date>"`.
  2. Document the replacement in the field description.
  3. Continue populating it for one minor-version cycle.
* The `CHANGELOG.md` lists every change with date and reason.
* Consumers can `WHERE deprecated = false` to ignore retired fields.

### 1.3 Field additions
* Any new field must come with: title, description, examples, units (if
  applicable), `nullable_reason`, source precedence rule.
* A new field is also added to `SCHEMA_GUIDE.md` with the rationale.
* Catalog (Phase 1) must be re-run; any rule in `validate_v2.py` that
  touches the field gets a rule_id.

---

## 2. Validation tiers

Every check in `ipo_portal/validate_v2.py` has one of four severities.
The severity controls what happens to the record at build time:

| Tier        | Lands in `data/site_v2/` | Lands in `audit/` | Alerts |
|-------------|-------------------------|-------------------|--------|
| `info`      | Yes                     | Yes               | No     |
| `warning`   | Yes                     | Yes               | No     |
| `error`     | Yes (with `data_quality.state = "review"`) | Yes | Daily digest |
| `blocking`  | **No** (quarantined)    | Yes               | Immediate (manifest flag, Slack TBD) |

Each check has a stable `rule_id` like `E.ID.001`. Hits are recorded as
`{rule_id, severity, message, observed_at, evidence}` in the record's
`data_quality.warnings[]` / `errors[]` arrays. Rule IDs never get
reused — even after a check is removed, the ID is reserved.

A record can have many findings; the highest severity wins for the
record's `state`.

### 2.1 Quarantine
* Blocking-tier failures route the record to
  `data/site_v2/quarantine/{slug}.json` with the full envelope plus the
  blocking finding. Manifest summarizes counts.
* Quarantined records do **not** appear in any public index.
* A reviewer-only `/quarantine/` route can surface them for triage.

---

## 3. Documentation contract on every record

Every JSON document the orchestrator or normalizer emits carries the
metadata envelope defined in `ipo_portal/orchestrator/metadata.py`. The
contract is binding:

```jsonc
{
  "$schema": "https://ipo-watch.local/schema/v2/issue.schema.json",
  "schema_version": "2.0.0",
  "schema_url_self": "...",
  "dataset": "ipo-watch.issues",
  "dataset_version": "v2026.05.23-1234",
  "generated_at": "2026-05-23T12:34:56+00:00",
  "generated_by": "ipo_portal.orchestrator/0.1.0",
  "time_zone": "Asia/Kolkata",
  "currency": "INR",
  "language": "en-IN",
  "sources": [ /* SourceRef[] */ ],
  "field_provenance": { /* path -> {source, snapshot_at, rule_id} */ },
  "data_quality": { "state": "...", "errors": [], "warnings": [] },
  "freshness": { /* source -> last_success_iso */ },
  "license": "...",
  "notes": null,
  /* ... body fields ... */
}
```

`field_provenance` is non-optional for any record where two or more
sources could write to the same field. The precedence rule actually
applied is recorded so disputes are replayable.

---

## 4. Replayability and idempotency

* **Raw snapshots are immutable**: `data/raw/<source>/<endpoint>/
  <timestamp>.json` is never edited.
* **All builds are deterministic given inputs**: `data/site_v2/` is
  fully reconstructable from `data/raw/` + the orchestrator code + the
  DeepSeek cache.
* **All writes are hash-gated**: identical content does not rewrite
  the file, preventing no-op git diffs and preserving inode mtimes.
* **DeepSeek calls are disk-cached** by SHA256 of inputs; reruns cost
  nothing.

---

## 5. Drift detection

`ipo_portal/orchestrator/drift.py` runs after every fetch cycle.

For each fresh snapshot, it walks the JSON and compares the set of
leaf-field paths against the canonical catalog (`docs/schema/raw_catalog/
<source>/<endpoint>.json`).

Three diff kinds:
* **Added paths** — new fields appeared upstream. Log to
  `data/reports/upstream_drift.jsonl`. Re-run catalog for the endpoint;
  the field gets a draft entry that a human reviews before promotion to
  the canonical schema.
* **Removed paths** — expected fields missing. Raise validation
  error tier `E.UPS.002`. Normalizer keeps populating from older
  snapshots until cleared.
* **Type changes** — same path, different value type. Log as
  `E.UPS.003` warning; coercion absorbs it but consumers are alerted.

Drift events feed a weekly digest review.

---

## 6. Source registry

`docs/data/SOURCES.md` documents every upstream endpoint with:
* URL pattern, referer requirements, HTTP method.
* Expected refresh cadence (e.g., hourly, daily, on-event).
* Staleness tolerance (e.g., 6h for hot endpoints, 7d for cold).
* Known quirks (rate limits, captcha behavior, weekend gaps).
* Required cookies / warm-up requests.
* Confidence tier for each field this source provides.

New sources are added via PR that updates `SOURCES.md`,
`SOURCE_PRECEDENCE.yaml`, `validate_v2.py` (any new rules), and the
catalog under `docs/schema/raw_catalog/<source>/`.

---

## 7. Identifier policy

Canonical identifiers in priority order:

1. **ISIN** — when present, unambiguous and stable.
2. **Stable join key** — `(source, source_record_id, listing_year)` for
   exchange-internal IDs.
3. **Normalized company name + listing date window** — last resort.

Slugs are `<normalized-name>-<short-id>` where `short-id` is the first
6 chars of `sha1(stable_join_key)`. This survives company renames; the
old slug joins via `aliases[]`. 301 redirects are computed from
`aliases[]` at site-build time.

---

## 8. Time, money, locale

* **All dates** are ISO 8601 (`YYYY-MM-DD` for date-only,
  `YYYY-MM-DDTHH:MM:SS±HH:MM` for instants). All times are stored in UTC
  with explicit offset; the original IST instant is preserved in
  `<field>_local` only where the IST clock-time matters (e.g., market
  open/close).
* **All monetary values** are stored as integer **paise** (₹ × 100) in
  a field named `<concept>_paise`. A computed `<concept>_inr_text`
  formats it for display ("₹15.00 Cr"). Never store as float.
* **Subscription multiples** stored as Decimal(precision=10, scale=4)
  in fields named `<concept>_x`.
* **Percentages** stored as integer basis-points (1% = 100) in fields
  named `<concept>_bps`.
* **Locale**: `en-IN`. Indian-style number formatting is presentation-
  layer only, never storage.

---

## 9. Test contracts

* **Golden fixtures**: For every source, `tests/fixtures/<source>/
  <endpoint>/<scenario>.json` is a saved raw snapshot. The normalizer
  emits to `tests/expected/<source>/<endpoint>/<scenario>.json` and
  the test asserts equality.
* **Drift tests**: For every catalog, a test asserts that a fresh
  snapshot's field set is a subset (no removals) and surfaces additions
  for review.
* **Validation tests**: Each `validate_v2.py` rule has at least one
  positive and one negative fixture.

Regression policy: A bug fix must include a golden fixture that
exercises the bug — the test fails before the fix, passes after.

---

## 10. Operational guardrails

### 10.1 Rate limits
* NSE: warm-up request before any API call (already implemented).
* BSE: max 1 request per 500ms (token bucket); back off on 429.
* Capitalmarket: max 1 page per 1s; respect their session timeout.

### 10.2 Failure modes
* A scraper failure does not silently overwrite good data. The
  scraper writes only successful responses; failures append to
  `data/reports/fetch_failures.jsonl`.
* A cron failure trips the manifest's `degraded` flag (see E.SNP.002).
* Two consecutive degraded builds trip an alert (TBD).

### 10.3 Secrets
* All API keys live in `.env` (gitignored). `.env.example` is the
  template, committed.
* No key ever appears in code, logs, or any data file. The
  `deepseek_usage.jsonl` log includes purpose + token counts only.
* Keys are rotated quarterly or immediately on any leak (e.g., the
  disposable DeepSeek key used for the v2 build is rotated after
  cutover).

---

## 11. Adding a new consumer

If another website / app wants to consume v2 data:

1. Read `DATASET.md` for the dataset overview.
2. Read `SCHEMA_GUIDE.md` for field semantics.
3. Validate ingested records against the JSON Schema at the URL in
   each record's `$schema` field.
4. Branch consumer behavior on `data_quality.state` if needed.
5. Pin to `dataset_version` for reproducibility; subscribe to
   `CHANGELOG.md` for upgrade triggers.

Consumers must **not** ingest records with `data_quality.state =
"quarantined"` (they aren't published anyway).

---

## 12. Decision log

Every notable design choice (precedence rule, slug strategy, status
enum, etc.) is documented as an Architecture Decision Record at
`docs/decisions/NNN-<slug>.md`. Format:

```
# NNN — <decision in one line>
**Status:** Accepted | Superseded by NNN | Deprecated
**Date:** YYYY-MM-DD
**Context:** ...
**Decision:** ...
**Consequences:** ...
**Related:** EDGE_CASES E.XXX.NNN, schema sections, etc.
```

Numbering is monotonic. A superseded decision is marked but not
deleted.
