# IPO Watch — Astro site data-access brief

**Hand this whole file to the Claude Code instance building the Astro
site.** It is self-contained: it explains where the data lives, its
shape, the storage conventions you must respect, and how to wire it into
Astro. You do not need the conversation that produced the data.

---

## 0. What this data is

A Python pipeline (in a separate project) produces a clean, validated,
canonical dataset of Indian IPOs/FPOs/OFS/rights/buybacks/NCDs at:

```
/home/bhuvanesh.r/Documents/Bhuvan projects/IPO/data/site_v2/
```

Treat that directory as **read-only input**. Every file is JSON. Each
record is self-describing (carries its own schema URL, sources,
provenance, freshness). ~4,600 issue records + ~3,000 companies +
per-issue subscription trajectories + rich RHP-prospectus extractions.

Authoritative references (read these in the IPO project if you have
access — they are the source of truth, this brief summarizes them):
- `docs/CONSUMER_GUIDE.md` — consumer-facing field guide
- `docs/data/IPO_PAGE_SCHEMA.md` — the rich-page data contract
- `docs/schema/v2/*.schema.json` — JSON Schemas (validate against these)

---

## 1. Directory layout

```
data/site_v2/
├── manifest.json                     # dataset version, counts, freshness
├── issues/
│   ├── index.json                    # flat array of ALL issues (compact summaries)
│   ├── by-slug/<slug>.json           # FULL record per issue  ← issue detail page
│   ├── by-year/<YYYY>.json           # issues grouped by year (+ index.json)
│   ├── by-status/<state>.json        # filed|open|closed|listed|upcoming|withdrawn
│   ├── by-kind/<kind>.json           # ipo|ofs|rights|buyback|ncd|invit|reit|fpo
│   └── <slug>/prospectus.json        # OPTIONAL rich RHP extraction (current IPOs)
├── companies/
│   ├── index.json                    # all companies
│   └── by-slug/<slug>.json           # company + its issues
├── trajectories/<slug>.json          # OPTIONAL subscription time-series
└── quarantine/<slug>.json            # failed validation — DO NOT render
```

**Index files** hold *compact summaries* (enough for cards/lists).
**`by-slug/<slug>.json`** holds the *full* record. Follow `url_path`.

---

## 2. The metadata envelope (every record opens with this)

```jsonc
{
  "$schema": "https://ipo-watch.local/schema/v2/issue.schema.json",
  "schema_version": "2.0.0",
  "dataset": "ipo-watch.issues",
  "dataset_version": "v2026.05.23-1015",   // pin this for reproducible builds
  "generated_at": "2026-05-23T10:15:00+00:00",
  "time_zone": "Asia/Kolkata",
  "currency": "INR",
  "sources": [{ "source": "nse", "endpoint": "...", "snapshot_at": "..." }],
  "field_provenance": { "pricing.issue_price_paise": { "source": "bse" } },
  "data_quality": { "state": "clean", "errors": [], "warnings": [] },
  "freshness": { "nse": "2026-05-23T09:00:00+00:00" },
  // ...then the body sections...
}
```

**Quality filter (do this everywhere):** only render records with
`data_quality.state in ("clean", "review")`. (Quarantined records aren't
in the indexes anyway, but check defensively.)

---

## 3. Storage conventions — READ THIS BEFORE RENDERING NUMBERS

The data stores canonical machine units, **not display values**. If you
render a `_paise` field as rupees you will be off by 100×.

| Field suffix | Stored as | To display |
|---|---|---|
| `_paise` | integer paise (₹ × 100) | `value / 100`, then format ₹ |
| `_x` | decimal **string** (e.g. `"7.5700"`) | append `×` → `7.57×` |
| `_bps` | integer basis points (1% = 100) | `value / 100`, append `%` |
| `_inr_text` | pre-formatted display string | use as-is |
| dates | ISO 8601 (`YYYY-MM-DD` or instant w/ offset) | format per locale |

Worked examples:
- `issue_price_paise: 31500` → **₹315.00**
- `listing_gain_bps: 1455` → **+14.55%**
- `overall_times_x: "191.9300"` → **191.93×**
- `issue_size_paise: 3000000000000` → **₹3,000 Cr**

Indian money formatting (use lakh/crore):
```ts
export function formatINR(paise: number | null | undefined): string {
  if (paise == null) return "—";
  const rupees = paise / 100;
  if (rupees >= 1e7) return `₹${(rupees / 1e7).toFixed(2)} Cr`;
  if (rupees >= 1e5) return `₹${(rupees / 1e5).toFixed(2)} L`;
  return `₹${rupees.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
}
export const formatBps = (bps?: number|null) => bps==null ? "—" : `${bps>=0?"+":""}${(bps/100).toFixed(2)}%`;
export const formatX   = (x?: string|null) => x==null ? "—" : `${parseFloat(x).toFixed(2)}×`;
```

**Every field is optional.** A record only carries what its sources
reported. Always null-check; never assume a field exists.

---

## 4. Issue record shape (`by-slug/<slug>.json`)

```jsonc
{
  /* ...envelope... */
  "slug": "aadhar-housing-finance-b883dd",
  "identity": {
    "company_name": "Aadhar Housing Finance Limited",
    "slug": "aadhar-housing-finance-b883dd",
    "symbol": "AADHARHFC",                 // may be null
    "isin": "INE883F01010",                // may be null
    "issue_type": "IPO",                   // IPO|FPO|Rights|Buyback|OFS|NCD|SGB|InvIT|REIT|Others
    "board_type": "Main Board",            // "Main Board" | "SME Board" | null
    "status": "Listed",                    // Filed|Open|Closed|Listed|Withdrawn|Upcoming
    "aliases": ["bse:scrip_code:544176", "kite:nse:AADHARHFC"]
  },
  "pricing": {
    "price_band_lower_paise": 30000, "price_band_upper_paise": 31500,
    "issue_price_paise": 31500,
    "issue_size_paise": 3000000000000,
    "face_value_paise": 1000, "market_lot": 47, "min_bid_qty": 47,
    "tick_size_paise": 100
  },
  "timeline": {
    "open_date": "2024-05-08", "close_date": "2024-05-10",
    "listing_date": "2024-05-15", "allotment_date": "2024-05-13"
  },
  "subscription": {
    "overall_times_x": "26.7600",
    "qib_times_x": "...", "retail_times_x": "...", "hni_times_x": "...",
    "anchor": { "allotment_paise": ..., "investors": [...] },
    "categories": [ { "category": "qib_excluding_anchor", "times_x": "..." } ]
  },
  "listing_performance": {
    "listing_open_price_paise": ..., "listing_close_price_paise": 32955,
    "current_price_paise": 47500,
    "listing_gain_bps": 1455,             // listing close vs issue price
    "current_gain_bps": 16000             // current vs issue price
  },
  "documents": {
    "drhp_url": "https://...", "rhp_url": "https://...",
    "prospectus_url": "https://...", "basis_allotment_url": "https://..."
  },
  "classification": {                      // sector/industry for filters & category pages
    "sector": "Pharmaceuticals",           // may be null
    "industry": "Pharmaceutical Industry",
    "sub_industry": null,
    "source": "rhp"                        // "rhp" (from prospectus) | "deepseek-classification"
  }
}
```

`classification` is present on most issues. `source: "rhp"` means it came
from the prospectus extraction (highest quality); otherwise it's the
DeepSeek bulk classifier. `market_lot` is the lot size in shares (the old
`lot_size_shares` name is gone).

`status` and `issue_type` are closed enums (see the JSON Schema for the
full list + descriptions). `board_type` distinguishes Main Board from
SME — SME issues list on only one exchange, so don't assume both.

### Additional blocks (present on issues we have per-issue detail for)

```jsonc
"parties": {                 // the "dealmakers"
  "lead_managers": ["INTERACTIVE FINANCIAL SERVICES LIMITED"],
  "co_lead_managers": [...], "registrar": "BIGSHARE SERVICES PRIVATE LIMITED",
  "sponsor_bank": "KOTAK BANK", "syndicate_members": [...]
},
"book_building": {
  "daily_subscription": [ { "day": 1, "times_x": "0.45" } ],
  "demand_schedule":   [ { "price_paise": 9100, "quantity": 708000, "cumulative_quantity": null } ]
},
"subscription": {
  "overall_times_x": "2.7000",
  "consolidated": {                       // NSE+BSE combined book
    "total_times_x": "2.7000",
    "categories": [ { "category": "qib", "shares_offered": 54000, "shares_bid": 108000, "times_x": "2.0000" },
                    { "category": "nii_gt_10l", ... }, { "category": "retail", ... } ]
  },
  "by_exchange": { "bse": { "categories": [...], "total_times_x": "..." } }  // per-exchange book
}
```

Category keys you'll see: `qib` (+ `qib_fii`/`qib_dfi`/`qib_mf`/`qib_other`),
`nii`, `nii_gt_10l`, `nii_lte_10l`, `retail`, `employee`, `shareholder`,
`policyholder`. Render the consolidated book as the headline subscription
table; offer the per-exchange book as a NSE-vs-BSE toggle. **All optional**
— present only for issues with bid-book data.

---

## 5. The differentiator: prospectus extraction (`<slug>/prospectus.json`)

For current IPOs we extract rich, **page-cited** data from the RHP PDF.
This is the moat — every fact carries a source citation you should
surface so readers can verify.

Each leaf is a provenance block:
```jsonc
{
  "value": "Online marketing and distribution of dental products via Dentalkart.com.",
  "raw_excerpt": "We are in the business of marketing and distribution of …",
  "source_page": 99,
  "source_section": "Our Business",
  "confidence": "high"        // high | medium | low
}
```

**Render `value`; show `raw_excerpt` + `source_page` as a tooltip /
footnote like "Source: RHP p.99". Treat `value: null` or
`confidence: "low"` as "not available" — never fabricate.**

Top-level sections: `hero`, `company_about`, `the_offer`,
`industry_landscape`, `macros`, `business_model`, `financial_highlights`,
`valuation`, `risks`, `promoter_and_shareholding_and_litigation`, `meta`.

The shape is codified in **`docs/schema/v2/prospectus.schema.json`**
(draft 2020-12) — the envelope + section set + the `provenance_leaf`
shape are the stable contract; section internals are permissive
(`additionalProperties:true`) because the extractor evolves. Validate
against it if you want a build-time guard; all extracted files pass it.

`prospectus.json` is **optional** — only present for issues we've
extracted (current/recent). A section may also be absent if its
extraction pass failed. Render a section only if it's present.

`risks` example you'll render as a categorized list with severity +
page citations:
```jsonc
"risks": {
  "total_disclosed_count": { "value": 69, "source_page": 26, ... },
  "by_category": [
    { "category": "Operational", "count": 20,
      "top_risks": [ { "title": {...}, "summary": {...},
                      "severity_inferred": {"value":"high"}, "provenance": {...} } ] }
  ]
}
```

---

## 6. Trajectory (`trajectories/<slug>.json`) — subscription over time

Optional. Hourly bid-book observations for issues that were active:
```jsonc
{
  "issue_slug": "...", "frozen_at": null,
  "observations": [
    { "observed_at": "2026-05-21T10:00:00+00:00", "source": "bse",
      "categories": { "qib": {"times": ...}, "retail": {...}, "nii": {...} },
      "total": {...} }
  ]
}
```
Use for the live "subscription building up" chart on open IPOs.

---

## 7. How to wire it into Astro

**Recommended access pattern:** a thin data layer that reads the JSON at
build time, behind a configurable data dir.

`.env` in the Astro project:
```ini
IPO_DATA_DIR=/home/bhuvanesh.r/Documents/Bhuvan projects/IPO/data/site_v2
```

`src/lib/ipodata.ts`:
```ts
import fs from "node:fs";
import path from "node:path";
const ROOT = process.env.IPO_DATA_DIR!;
const read = (p: string) => JSON.parse(fs.readFileSync(path.join(ROOT, p), "utf-8"));

export const allIssues   = () => read("issues/index.json").items as IssueSummary[];
export const issue       = (slug: string) => read(`issues/by-slug/${slug}.json`) as Issue;
export const prospectus  = (slug: string) => {
  const p = path.join(ROOT, "issues", slug, "prospectus.json");
  return fs.existsSync(p) ? JSON.parse(fs.readFileSync(p, "utf-8")) : null;
};
export const byKind   = (k: string) => read(`issues/by-kind/${k}.json`).items;
export const byYear   = (y: string) => read(`issues/by-year/${y}.json`).items;
export const byStatus = (s: string) => read(`issues/by-status/${s}.json`).items;
export const company  = (slug: string) => read(`companies/by-slug/${slug}.json`);
```

Static pages with `getStaticPaths`:
```ts
// src/pages/ipo/[slug].astro
export async function getStaticPaths() {
  return allIssues()
    .filter(i => ["clean","review"].includes(i.data_quality_state))
    .map(i => ({ params: { slug: i.slug } }));
}
const issueData = issue(Astro.params.slug);
const rhp = prospectus(Astro.params.slug);   // may be null
```

**Type the records** off the JSON Schemas in `docs/schema/v2/` (you can
generate TS types from them with `json-schema-to-typescript`).

### Production / Cloudflare deployment

The data dir is gitignored in the pipeline repo and is large, so for
deploys pick one:
1. **Build-artifact sync (simplest first):** the pipeline's CI uploads
   `data/site_v2/` as an artifact; the Astro build downloads it and sets
   `IPO_DATA_DIR` to the download path. Fully static output to Cloudflare
   Pages.
2. **R2 + fetch at build:** pipeline pushes `data/site_v2/` to an R2
   bucket; Astro fetches index + per-slug JSON at build. Scales best;
   the pipeline was designed with an R2 escape hatch in mind.
3. **Monorepo / submodule:** check the data in as a submodule. Simplest
   mentally, heaviest on git.

Start with (1) for the first deploy; move to (2) when issue count grows.

---

## 8. Pages to build (suggested)

| Route | Source | Notes |
|---|---|---|
| `/ipo/[slug]/` | `by-slug/<slug>.json` + `prospectus.json` | The hero page. Lead with the prospectus richness + "Source: RHP p.X". |
| `/` (home) | `by-status/open.json`, `upcoming.json`, recent `listed` | Current + upcoming IPOs. |
| `/ipos/[year]/` | `by-year/<YYYY>.json` | Yearly archive. |
| `/category/[kind]/` | `by-kind/<kind>.json` | OFS / rights / buyback / NCD / etc. |
| `/company/[slug]/` | `companies/by-slug/<slug>.json` | Company + all its issues. |

**Design note** (the site owner's standing preference): editorial,
newspaper-front aesthetic — FT/Bloomberg Businessweek/Economist print.
Serif everywhere, hairline rules, single accent colour, compact listing
rows (~70–90px), heroic numbers on detail pages, radical restraint (one
column + one sidebar, no card-grid clutter). White background; strong
dark mode later. Confirm specifics with the owner.

---

## 9. Gotchas (will bite you if ignored)

1. **Units.** `_paise` / `_bps` / `_x` are machine units. §3. The #1 bug.
2. **Everything is nullable.** Null-check every field.
3. **SME = single exchange.** SME issues list on NSE *or* BSE, not both.
   `board_type: "SME Board"`. Don't render "NSE & BSE" for them.
4. **`status: "Filed"`** means a DRHP exists but the issue hasn't
   opened/listed — show it as "Filed with SEBI", not "Listed".
5. **`prospectus.json` and `trajectories/` are optional.** Guard with
   existence checks.
6. **Provenance is the product.** Surface `source_page` citations on
   prospectus-derived facts. That's what makes this better than every
   other IPO site.
7. **Pin `dataset_version`** from `manifest.json` in your build so a
   mid-build data refresh doesn't produce inconsistent pages.
8. **Validate in dev.** Each record's `$schema` points to its JSON
   Schema; validate a sample during development to catch shape drift.
9. **Sparse pricing on the long tail is normal, not a bug.** The dataset
   spans all issuance types since the 1990s. Headline fields are well-
   covered on recent main-board IPOs but sparse across the historical /
   NCD / OFS / buyback tail (dataset-wide: band ~30%, issue_price ~39%,
   issue_size ~16%, overall subscription ~24%, listing_gain ~80% of
   Listed). Render `—` gracefully; never infer a missing number.
10. **For current IPOs, prefer the prospectus for offer details.** When
    `pricing.issue_size_paise` is null, `prospectus.json` →
    `hero.total_offer_paise` / `the_offer.{fresh_issue,ofs,total_offer}`
    is the higher-quality, page-cited source. Exchange feed is the
    fallback, not the other way around, on the detail page.

---

## 10. First task for the new instance

1. Confirm `IPO_DATA_DIR` resolves and `issues/index.json` loads.
2. Generate TS types from `docs/schema/v2/issue.schema.json`.
3. Build `src/lib/ipodata.ts` (§7) + the formatters (§3).
4. Build `/ipo/[slug]/` for one slug end-to-end — render identity,
   pricing, timeline, listing performance, then the prospectus sections
   with page-citation footnotes.
5. Then the listing pages (§8).

Ask the owner before inventing data shapes — if a field you want isn't
in the records, it can be added to the pipeline rather than faked.
