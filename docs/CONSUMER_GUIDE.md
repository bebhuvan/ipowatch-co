# Consumer guide — building on data/site_v2/

How the IPO Watch Astro site (or any external consumer / LLM agent)
should read the v2 dataset.

## Where to start

For an **issue page** (`/ipo/<slug>/`), read two files:

1. `data/site_v2/issues/by-slug/<slug>.json` — the canonical issue
   record (exchange-feed data: identity, pricing, timeline,
   subscription, listing performance, document URLs).
2. `data/site_v2/issues/<slug>/prospectus.json` — *optional* rich
   prospectus extraction (company story, industry, financials, risks,
   valuation), present only for issues we've run RHP extraction on.
   Each field carries page-citation provenance.

For **listing pages**, read the indexes:

* `issues/index.json` — every issue, compact summary, newest first.
* `issues/by-year/<YYYY>.json`, `by-status/<state>.json`,
  `by-kind/<kind>.json` — pre-bucketed views.
* `companies/index.json`, `companies/by-slug/<slug>.json`.

## The metadata envelope (every record)

Every JSON opens with envelope keys before the body. Read these first:

```jsonc
{
  "$schema": "https://ipo-watch.local/schema/v2/issue.schema.json",
  "schema_version": "2.0.0",
  "dataset": "ipo-watch.issues",
  "dataset_version": "v2026.05.23-1015",
  "generated_at": "2026-05-23T10:15:00+00:00",
  "time_zone": "Asia/Kolkata",
  "currency": "INR",
  "sources": [{ "source": "nse", "endpoint": "...", "snapshot_at": "..." }],
  "field_provenance": { "pricing.issue_price_paise": { "source": "bse", "rule_id": "E.SRC.002" } },
  "data_quality": { "state": "clean", "errors": [], "warnings": [] },
  // … then identity, pricing, timeline, subscription, …
}
```

**Filtering rule for consumers:** ingest `data_quality.state in
('clean', 'review')`. Records in `quarantine/` are never in the public
indexes.

## Storage conventions (IMPORTANT)

Read [`docs/decisions/001-canonical-storage-units.md`] before rendering
numbers. In short:

| Field suffix | Meaning | To display |
|---|---|---|
| `_paise` | Integer paise (₹ × 100) | `÷ 100`, then format ₹ |
| `_x` | Decimal string, subscription multiple | append `×` |
| `_bps` | Integer basis points | `÷ 100`, append `%` |
| `_inr_text` | Pre-formatted display string | use as-is |
| dates | ISO 8601 (date or instant w/ offset) | format per locale |

Example: `issue_price_paise: 31500` → `₹315.00`. `listing_gain_bps:
1455` → `+14.55%`. `overall_times_x: "191.9300"` → `191.93×`.

Never re-derive units; never treat `_paise` as rupees.

## Issue record shape

```jsonc
{
  "identity": {
    "company_name": "Aadhar Housing Finance Limited",
    "slug": "aadhar-housing-finance-b883dd",
    "symbol": "AADHARHFC",
    "isin": "INE…",            // may be null
    "issue_type": "IPO",        // IPO|FPO|Rights|Buyback|OFS|NCD|SGB|InvIT|REIT|Others
    "board_type": "Main Board", // Main Board | SME Board
    "status": "Listed",         // Filed|Open|Closed|Listed|Withdrawn|Upcoming
    "aliases": ["bse:scrip_code:544176", "bse:stock_page:https://…"]
  },
  "pricing": { "issue_price_paise": 31500, "price_band_lower_paise": …, "issue_size_paise": … },
  "timeline": { "open_date": "2024-05-08", "close_date": "2024-05-10", "listing_date": "2024-05-15" },
  "subscription": { "overall_times_x": "26.7600", "anchor": {…}, "categories": [{ "category": "qib_excluding_anchor", "times_x": … }] },
  "listing_performance": { "listing_close_price_paise": …, "current_price_paise": …, "listing_gain_bps": 1455, "current_gain_bps": 16000 },
  "documents": { "drhp_url": "…", "rhp_url": "…", "prospectus_url": "…", "basis_allotment_url": "…" }
}
```

Any field may be absent (a record only carries what its sources
reported). Render defensively: check presence, don't assume.

## Prospectus extraction shape

`issues/<slug>/prospectus.json` follows
[`docs/data/IPO_PAGE_SCHEMA.md`]. Every leaf is a provenance block:

```jsonc
{
  "value": "Vasa Denticity Limited markets dental products via Dentalkart.com.",
  "raw_excerpt": "We are in the business of marketing and distribution of …",
  "source_page": 99,
  "source_section": "Our Business",
  "confidence": "high"
}
```

Render the `value`; show `raw_excerpt` + `source_page` as a "Source:
RHP p.99" tooltip / footnote. **Treat `confidence: "low"` or
`value: null` as "not available" — never fabricate.**

Sections: `hero`, `company_about`, `the_offer`, `industry_landscape`,
`macros`, `business_model`, `financial_highlights`, `valuation`,
`risks`, `promoter_and_shareholding_and_litigation`, plus `meta`
(extraction provenance, cost, pages).

## Validating ingested records

Each record's `$schema` points to its JSON Schema under
`docs/schema/v2/`. To validate:

```python
import json, jsonschema
schema = json.load(open("docs/schema/v2/issue.schema.json"))
record = json.load(open("data/site_v2/issues/by-slug/<slug>.json"))
# Validate the canonical sections (envelope keys are validated separately)
for section in ("identity","pricing","timeline","subscription","listing_performance","documents"):
    if section in record:
        jsonschema.Draft202012Validator(schema["properties"][section]).validate(record[section])
```

## Versioning & reproducibility

* Pin to `dataset_version` for a reproducible snapshot.
* Watch `docs/data/CHANGELOG.md` for schema changes.
* MAJOR schema bumps move to a new tree (`data/site_v3/`); v2 keeps
  producing during the deprecation window.

## For LLM agents

Every record is self-describing: read one `by-slug/<slug>.json` in
isolation and the envelope tells you what it is, where each field came
from (`field_provenance`), how fresh it is (`generated_at`,
`sources[].snapshot_at`), and how much to trust it (`data_quality`).
The prospectus extraction adds page-cited provenance per fact, so you
can quote IPO Watch data with a verifiable source reference.
