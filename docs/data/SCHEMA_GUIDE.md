# Schema guide

Per-field semantics for the v2 IPO Watch dataset. This is the
human-readable companion to the JSON Schemas under `docs/schema/v2/`.

> **State of this doc.** This file is being filled in as part of the
> v2 rebuild. Sections marked **TBD** will be completed once Phase 2
> (canonical schema design) finalizes the canonical schema in
> `docs/schema/v2/`. The JSON Schemas remain the authoritative
> contract; this doc explains them in prose.

## How to read each field

Every field entry below has:

* **Path** — dotted location in the v2 record (e.g.,
  `pricing.price_band.upper_paise`).
* **Type** — JSON Schema type + format.
* **Units** — canonical storage unit (`paise`, `bps`, `x`, `shares`, etc.).
* **Sources** — which upstream endpoints can populate this field, and
  the precedence applied when multiple disagree
  (see [`SOURCE_PRECEDENCE.yaml`](SOURCE_PRECEDENCE.yaml)).
* **Nullable** — when null is meaningful vs. unavailable.
* **Validation** — `validate_v2` rule IDs that fire on this field.
* **Display** — how the site renders it (and which display helper).

## Top-level metadata envelope

Every issue and company record opens with the same envelope keys —
documented once here and not repeated per field.

| Key | Type | Notes |
|---|---|---|
| `$schema` | URL | Stable URL to the JSON Schema validating this document. |
| `schema_version` | semver string | `"2.0.0"` for the first cut. |
| `schema_url_self` | URL | Where this document lives. |
| `dataset` | string | `"ipo-watch.issues"` / `"ipo-watch.companies"` / etc. |
| `dataset_version` | string | Date-stamped build version. |
| `generated_at` | ISO instant | UTC, explicit offset. |
| `generated_by` | string | `"ipo_portal.orchestrator/0.1.0"`. |
| `time_zone` | string | Always `"Asia/Kolkata"`. |
| `currency` | string | Always `"INR"`. |
| `language` | string | Always `"en-IN"`. |
| `sources[]` | array | Every contributing upstream source. |
| `field_provenance{}` | map | Source that won each disputed field. |
| `data_quality{}` | object | `{state, errors[], warnings[], info[]}`. |
| `freshness{}` | map | Last successful refresh per source. |
| `license` | string | CC-BY-4.0 default; see [`DATASET.md`](DATASET.md). |
| `notes` | string \| null | Free-text. |

## Issue record (v2.0.0) — TBD

To be populated from `docs/schema/v2/issue.schema.json` once Phase 2
finalizes. Sketch of the planned shape:

```
identity:
  isin                  — ISO 6166 ISIN; null if not yet allotted.
  symbol_nse            — NSE trading symbol; null if not on NSE.
  symbol_bse            — BSE Scrip Code; null if not on BSE.
  scrip_code_bse        — BSE numeric ID.
  pan                   — Issuer PAN, structurally validated.
  slug                  — `<normalized-name>-<short-id>`.
  short_id              — 6-char sha1 prefix; survives renames.
  aliases[]             — Prior slugs for 301 redirects.
classification:
  issue_kind            — enum: ipo_mainboard | ipo_sme | ofs | rights | …
  status                — enum: upcoming | active | closed | listed |
                          withdrawn | rejected | postponed
  exchange_listings[]   — Per-exchange listing records.
company:
  display_name          — Reader-facing legal name.
  legal_name            — Filed legal name (may include "Limited").
  industry              — SEBI/exchange industry classification.
pricing:
  face_value_paise      — Per-share face value in paise.
  issue_price_paise     — Final issue price; null until book closes.
  price_band:
    lower_paise         — Band lower bound.
    upper_paise         — Band upper bound.
  lot_size_shares       — Minimum bid lot.
  minimum_bid_paise     — Lot × lower band.
timeline:
  open_date             — Subscription open (YYYY-MM-DD IST).
  close_date            — Subscription close.
  allotment_date        — DEMAT credit / allotment.
  refund_date           — Refund initiation.
  listing_date          — Exchange listing.
subscription:
  times_x               — Net-of-anchor subscription multiple.
  times_gross_x         — Gross subscription multiple (with anchor).
  by_category:
    qib_excluding_anchor: { times_x, shares_offered, shares_bid }
    anchor: { allotment_paise, shares_allotted, investors[] }
    nii: { times_x, shares_offered, shares_bid, gt_10l, lte_10l }
    retail: …
    employee: …
    shareholder: …
    policyholder: …
  by_exchange:
    nse: { times_x, total_shares_bid }
    bse: { times_x, total_shares_bid }
  trajectory:
    observations[]      — Time-series of subscription snapshots.
    frozen_at           — Freeze timestamp (close + 7d) or null.
issue_size:
  fresh_issue_paise     — Newly issued shares × price.
  ofs_paise             — Sale-by-shareholder shares × price.
  total_paise           — Sum.
listing_performance:
  listing_day_open_paise
  listing_day_close_paise
  listing_day_gain_bps
  current_price_paise
  current_gain_bps      — From listing day open.
  listing_day_circuit_locked  — bool; null if not applicable.
documents[]             — RHP, DRHP, prospectus, public ads, etc.
prospectus_extract      — Reference to data/site_v2/issues/<slug>/prospectus.json (if available).
```

## Company record — TBD

To be populated from `docs/schema/v2/company.schema.json`.

## Trajectory record — TBD

To be populated from `docs/schema/v2/trajectory.schema.json`.

## Aggregate records — TBD

Per-year, per-status, per-kind rollups.

## Display helpers

Storage is canonical (integer paise, integer basis points, ISO 8601).
Display formatting lives in the Astro site layer; the canonical record
**does not** carry pre-formatted display strings. The helpers below are
the recommended formatting policy:

| Storage | Display rule | Examples |
|---|---|---|
| `<concept>_paise` | If ≥ 10⁷ paise: `"₹X.YZ Cr"`. If ≥ 10⁵ paise: `"₹X.YZ L"`. Else: `"₹N"`. | `15_000_000_00 paise → "₹15.00 Cr"` |
| `<concept>_x` | Decimal with 2 decimals + trailing `"x"`. | `Decimal("7.5700") → "7.57x"` |
| `<concept>_bps` | bps / 100 with 2 decimals + `"%"`. | `1250 → "12.50%"` |
| Dates | Project default: `"21 May 2026"` (en-IN). | |
| Instants | IST clock-time + relative time. | `"04 May 2026, 09:30 IST"` |

## Versioning of this guide

This guide tracks the v2 schema. Major schema bumps (3.0.0+) get a
fresh document tree (`docs/schema/v3/`, `docs/data/SCHEMA_GUIDE.v3.md`).
See [`CHANGELOG.md`](CHANGELOG.md) for the version history.
