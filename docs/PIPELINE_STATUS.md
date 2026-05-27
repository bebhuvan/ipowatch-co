# IPO Watch — Data Pipeline: Complete Current Status

> **Purpose of this document.** A rigorous, end-to-end description of the data
> pipeline as it stands today (2026-05-24), written so that someone who has
> never seen this repo can understand exactly what exists, how data flows from
> the exchanges to the published JSON, what the invariants are, what has been
> fixed, and what is still open. It is the companion to
> `docs/CODEX_AUDIT_PROMPT.md`, which instructs a fresh reviewer to verify
> everything here from scratch.
>
> **Trust rule:** where this document and the code disagree, **the code is
> current**. This doc is a map, not the territory. Verify, don't assume.

---

## 0. The one-paragraph summary

We scrape every Indian primary-market issuance (IPO, FPO, OFS, rights, buyback,
NCD/debt public issue, REIT, InvIT, IPP/tender, SGB) from NSE and BSE — plus
SEBI (DRHP filings), Kite/Zerodha (prices), and aggregators (Moneycontrol,
Trendlyne, PRIME, CapitalMarket) — into immutable raw snapshots. A v2
normalizer consolidates those snapshots into one canonical record per issue,
in machine units (paise / basis points / decimal-string multiples), with a
metadata envelope and per-field provenance. The differentiator is **page-cited
verbatim extraction from RHP PDFs** via DeepSeek. Output is a static JSON tree
(`data/site_v2/`) consumed by an Astro site (`web/`). Two GitHub Actions
workflows refresh and commit the data behind a data-integrity gate.

Current scale: **7,252 issues, 5,119 companies, 293 trajectories**, manifest
counts equal on-disk file counts on all three trees. 141 tests pass.

---

## 1. Repository layout

```
IPO/
├── ipo_portal/                  Python package — all scraping + normalization
│   ├── cli.py                   v1 CLI: python -m ipo_portal {fetch,build-site,validate}
│   ├── sources.py               NSE/BSE endpoint registry (~68 endpoints) + nested discovery
│   ├── http.py                  HTTP client (cookies, retries, throttle)
│   ├── storage.py               raw snapshot writer, hash-gated atomic JSON writes
│   ├── normalize.py             v1 normalizer (legacy, feeds data/site/)
│   ├── site_builder.py          v1 site builder (legacy, data/site/)
│   ├── validate.py              v1 validation (legacy)
│   ├── backfill_detail.py       Sweeps BSE IPO_NO 1→7800 for historical per-issue detail
│   ├── capitalmarket.py prime.py trendlyne.py moneycontrol.py datahub.py   aggregator scrapers
│   ├── sebi.py                  SEBI DRHP-filings scraper
│   ├── kite.py kite_auth.py kite_v2.py   Zerodha Kite price fetch + TOTP login + v2 snapshot
│   ├── deepseek.py              DeepSeek API client (disk-cached, telemetry)
│   ├── trajectory.py            v1 SRNo→category mappers (reused by v2)
│   ├── normalization/           canonical unit parsers (units.py): paise/bps/dates/sanitize
│   ├── normalize_v2/            THE v2 pipeline (see §4)
│   │   ├── pipeline.py          run_normalize(): collect → consolidate → merge → validate → write
│   │   ├── identity.py          normalize_name, stable_join_key, slug
│   │   ├── precedence.py        source-precedence resolution
│   │   ├── schema_check.py      JSON Schema (draft 2020-12) structural gate
│   │   ├── indexes.py           builds issues/companies indexes
│   │   ├── trajectory_v2.py     subscription-over-time series
│   │   └── parsers/             one parser per source endpoint (see §3)
│   ├── validation_v2/           4-tier severity engine (info/warning/error/blocking)
│   └── orchestrator/            higher-level commands
│       ├── cli.py               python -m ipo_portal.orchestrator {normalize,refresh,enrich-rhp,
│       │                          drift,catalog,schema,audit,gap-scan,classify-sectors}
│       ├── refresh.py           THE cron entrypoint: fetch→kite→normalize→enrich→drift
│       ├── rhp_enrich.py        (superseded single-call extractor — see §6)
│       ├── sectors.py           DeepSeek bulk sector/industry classification
│       ├── drift.py             upstream-schema drift detector
│       ├── catalog.py           raw-field catalog generator (drift baseline)
│       └── metadata.py          the v2 envelope builder
├── scripts/
│   ├── extract_rich_rhp.py      THE RHP extractor (multi-pass, page-cited) — the differentiator
│   ├── audit_v2_quality.py      data-quality audit + CI gate (--gate)
│   └── patch_v2_schema*.py      one-off schema patchers
├── tests/                       12 test files, 141 tests (pytest)
├── docs/                        all documentation (see §11)
│   └── schema/v2/               issue / company / trajectory / prospectus / index schemas
├── data/                        all data (NOT in git — see §10)
├── web/                         Astro site (separate instance owns this)
├── .github/workflows/           refresh-data.yml (hourly), refresh-full.yml (daily)
├── requirements.txt             requests, beautifulsoup4, jsonschema, pyotp, pdfplumber
└── HANDOFF.md                   older Claude-session handoff (design history; partly pre-v2)
```

**Python 3.12.** Virtualenv at `.venv/`. Run Python via `.venv/bin/python`.
`pytest` is NOT in `.venv` — run tests with system `python3 -m pytest` (which
has pytest but lacks `pyotp`, so exclude the kite test:
`python3 -m pytest -q --ignore=tests/test_kite_auth.py`).

---

## 2. Data flow (end to end)

```
  NSE / BSE / SEBI / Kite / aggregators
        │  (ipo_portal fetch / kite / sebi)
        ▼
  data/raw/<source>/<endpoint>/<UTC-timestamp>.json   ← immutable snapshots (327 MB, 21,864 files)
        │  (ipo_portal.orchestrator normalize)
        ▼
  normalize_v2.pipeline.run_normalize:
     collect_contributions  → one Contribution per parsed source row
     consolidate            → union-find groups contributions into issues
     merge                  → precedence picks winning field values + provenance
     recompute gains        → derive gains from canonical prices
     apply sector           → prospectus sector, else DeepSeek sector_map
     schema validate        → structural gate (draft 2020-12)
     validation_v2 engine   → severity tiers; blocking → quarantine
     write + prune orphans
        ▼
  data/site_v2/
     manifest.json
     issues/by-slug/<slug>.json           ← 7,252 canonical issue records
     issues/index.json, by-year/, by-status/, by-kind/
     companies/index.json, companies/by-slug/<slug>.json   ← 5,119
     trajectories/<slug>.json             ← 293 subscription time-series
     issues/<slug>/prospectus.json        ← RHP extraction (current IPOs only)
        │  (enrich-rhp uses documents.rhp_url; scripts/extract_rich_rhp.py)
        ▼
  web/ (Astro) reads data/site_v2  →  ipowatch.co
```

`data/site/` (v1, 256 MB) is the **legacy** tree still produced by `fetch` and
still read by un-migrated Astro pages. It will be retired once the frontend
fully reads v2.

---

## 3. Sources & parsers

`ipo_portal/sources.py` registers ~68 NSE/BSE endpoints. Aggregators and Kite
have their own fetchers. Each source endpoint has exactly **one** parser in
`ipo_portal/normalize_v2/parsers/` (registry is one-parser-per-endpoint-key).

| Parser | Endpoint(s) | Emits |
|---|---|---|
| `nse_ipo_current/past/upcoming` | NSE IPO lists | identity, pricing, timeline, status |
| `nse_ofs / nse_rights / nse_tender / nse_ipp` | NSE non-IPO issuance lists | per-type records |
| `nse_offer_documents` | NSE offer-doc index | `documents.drhp_url` / prospectus links |
| `nse_bid_summary` | NSE `issue_detail_<sym>_<series>`, `(consolidated_)bid_details_<sym>` | `subscription.by_exchange.nse` **+ issue detail** (band, lot, face/tick, lead managers, sponsor, registrar, period, `documents.rhp_url`, full name from `issueInfo.dataList`) |
| `bse_public_issue_details` | BSE `GetPublicIssue` | identity, band, dates, `IR_flag`→issue_type (DPI→NCD, OTB→Buyback, CMN→Others) |
| `bse_issue_detail` | BSE `GetMkt_ISSUE_BBS_IPO` per-IPO_NO | lot/tick/face, BRLM/co-BRLM/registrar/sponsor/syndicate, demand schedule, `Security_Type`→issue_type |
| `bse_bid_summary` | BSE `(consolidated_)bid_details_<ino>` | `subscription.consolidated` **and** `subscription.by_exchange.bse` (keeps BOTH books) |
| `bse_ipo_performance` | BSE listing-day performance | listing/current price, gains |
| `kite_performance` | Kite v2 snapshot | `listing_performance.current_price_paise`, listing candles |
| `moneycontrol_listed` | Moneycontrol | enrichment prices/listing |
| `sebi_filings` | SEBI DRHP scrape | `documents.drhp_url`, "Filed" status |

**Join keys** (how a parser tells the consolidator which issue a row belongs to):
- BSE per-issue feeds → `bse_ipo_no:<IPO_NO>` (globally unique per issue — keying
  by name+year fused distinct issues and corrupted prices; this was a real bug).
- NSE per-issue feeds → `symbol:<SYMBOL>` (no IPO_NO in NSE; symbol union).
- List feeds → `IssueJoinKey` from `stable_join_key`: `isin:<ISIN>` > `pan_year:<PAN>:<YYYY>` > `name_year:<norm-name>:<YYYY>`.

`backfill_detail.py` recovered historical BSE per-issue detail by sweeping
IPO_NO 1→7800 — this is why `data/raw/bse/` has ~20,861 endpoint dirs.

---

## 4. The v2 normalization pipeline (`normalize_v2/pipeline.py`)

`run_normalize(raw_root, out_root, schema_root, precedence_path)`:

1. **collect_contributions** — load latest snapshot per endpoint, run its
   parser, get a list of `Contribution(source, endpoint, snapshot_at, join_key,
   fields={dotted.path: value})`.
2. **consolidate** — union-find over join keys, in this order:
   - ISIN unions, then alias unions (`bse:ipo_no`, `bse:scrip_code`, `nse:symbol`, `kite:*`).
   - Symbol-discriminated unions (NSE symbol ↔ canonical symbol).
   - **Name-cluster union** grouped by `(normalized_name, issue_type)` with an
     "effective year" (real date from timeline; fetch-year treated as wildcard).
   - **Hard boundary:** if a cluster spans ≥2 distinct `bse_ipo_no` values, union
     only *within* each ipo_no — never fuse distinct BSE issues.
3. **merge** — for each group, precedence (`SOURCE_PRECEDENCE.yaml`: primary
   exchange > secondary > enrichment) picks the winning value per dotted field;
   records `field_provenance[path] = {source, rule_id}`.
4. **post-merge derivations:**
   - `_recompute_gains` — listing/current gain (bps) derived from the canonical
     issue/listing/current prices (never trust a source's pre-computed gain).
   - `_infer_status` — timeline-based; offer-doc→Filed; bid-book→Closed fallback.
   - `_apply_sector` — prospectus `company_about.sector` (source=`rhp`) wins,
     else `data/derived/sector_map.json` keyed by normalized company name.
5. **slug assignment** — `_assign_unique_slugs`: the dated/richest record keeps
   the clean `name-<6hash>` slug; colliding date-less groups get a slug
   rehashed from their unique join key (prevents silent overwrite on write).
6. **validate** — JSON Schema structural gate (blocking → quarantine) + the
   `validation_v2` severity engine (info/warning/error/blocking).
7. **write + prune** — atomic hash-gated writes to `issues/by-slug/`; then
   `_prune_stale` deletes any by-slug / quarantine / company / trajectory file
   not produced this run (so manifest == disk always).
8. **indexes + trajectories + manifest.**

### Canonical conventions (the contract)
- **Money** → integer **paise** (`*_paise`). **Percent** → integer **basis
  points** (`*_bps`). **Subscription multiple** → **decimal-string** (`*_x`,
  e.g. `"26.7600"`). **Dates** → ISO 8601. **Timezone** IST. **Currency** INR.
- **slug** = `<normalized-name>-<sha1(stable_join_key)[:6]>`. `normalize_name`
  strips Ltd/Limited/Pvt/etc., diacritics, and the BSE `^\d+\+` row-marker
  artifact ("1+ZEE MEDIA" → "zee media").
- **Envelope** on every record: `$schema, schema_version, slug, sources[],
  field_provenance, data_quality, freshness, dataset_version, generated_at,…`.
- Enums: `issue_type` ∈ {IPO,FPO,Rights,Buyback,OFS,NCD,SGB,InvIT,REIT,Others};
  `status` ∈ {Filed,Open,Closed,Listed,Withdrawn,Upcoming};
  `board_type` ∈ {Main Board, SME Board}.

---

## 5. Validation & quality guardrails

Three independent layers:

1. **JSON Schema (structural)** — `docs/schema/v2/{issue,company,trajectory,
   prospectus,index}.schema.json`, draft 2020-12. A structural failure is
   blocking → record is quarantined, never published.
2. **`validation_v2` severity engine** — rule-based checks with immutable IDs
   (e.g. `E.ID.001`, `E.DOC.001`, `E.ENG.001`) and four severities
   (info/warning/error/blocking). Lives in `ipo_portal/validation_v2/`.
3. **`scripts/audit_v2_quality.py`** — the fast, stdlib-only regression
   tripwire (read-only). Checks enums, identity, pricing units/sanity, dates,
   status consistency, subscription, listing performance, thin records,
   duplicates, **and manifest⇄disk parity**. `--gate` exits non-zero ONLY on
   integrity-critical classes (`manifest.*`, `file.*`, `enum.*`,
   `identity.bad_*`) — this is the CI commit gate. Long-tail data quirks are
   informational, never block.

**Tests:** 12 files, 141 tests (`tests/`). Covers consolidation, identity,
precedence, normalization units, validation_v2, trajectory, drift, the BSE and
NSE detail parsers, moneycontrol, and safeguards.

---

## 6. RHP extraction — the differentiator (`scripts/extract_rich_rhp.py`)

- Multi-pass, section-targeted. Downloads the RHP PDF (cached by URL hash;
  **NSE serves zipped RHPs — `download_pdf` magic-byte-detects a ZIP and
  extracts the largest inner PDF**), `pdftotext` preserves page breaks, builds
  a char-offset→page index, slices by section, and calls DeepSeek per section.
- **Every scalar fact is a provenance leaf:** `{value, raw_excerpt,
  source_page, source_section, confidence}`. This is the moat — "Source: RHP
  p.99" beats "AI summary."
- 10 sections: `hero, company_about, the_offer, industry_landscape, macros,
  business_model, financial_highlights, valuation, risks,
  promoter_and_shareholding_and_litigation` (+ `meta`).
- Output: `data/site_v2/issues/<slug>/prospectus.json`. Schema:
  `docs/schema/v2/prospectus.schema.json` (validates all current files).
- **Go-forward only, no backfill** (~$18/yr): `enrich-rhp --scan-pending`
  resolves the URL from `documents.rhp_url || drhp_url`, enriches only current
  issues (Open/Upcoming or recently Listed/Filed), skips if `prospectus.json`
  exists. ~8 DeepSeek calls / ~$0.15 per RHP, disk-cached.
- Proven on 6 current RHPs (60–90 categorized risks each).
- `ipo_portal/orchestrator/rhp_enrich.py` is an **older single-call framework,
  superseded** by the rich script; the CLI's `_load_rhp_extractor` loads the
  rich one. (Candidate for deletion — verify nothing imports it.)

---

## 7. Prices (Kite) & sectors

- **Kite:** `kite_auth.py` does TOTP auto-login (pyotp); daily token (~06:00 IST
  expiry). `kite.py` fetches LTPs + listing-day candles into a SQLite DB;
  `kite_v2.py` exports a v2 snapshot under `data/raw/kite/` that
  `kite_performance` parses into `listing_performance`. Credentials
  (`KITE_API_KEY/API_SECRET/USER_ID/PASSWORD/TOTP_SECRET`) live in `.env` /
  GitHub secrets ONLY — never in code or chat.
- **Sectors:** `classify-sectors` (DeepSeek) classifies companies lacking a
  sector into a fixed vocabulary → `data/derived/sector_map.json` (keyed by
  normalized company name). `normalize` applies it (prospectus sector wins).
  97% of issues have a sector. Not in the cron — run periodically.

---

## 8. CI / deployment (`docs/CI_GOLIVE.md`)

- **`refresh-data.yml`** (hourly, `17 * * * *`): `refresh --skip-enrich` →
  SEBI + NSE/BSE fetch + Kite prices + v2 normalize + drift. No DeepSeek.
- **`refresh-full.yml`** (daily, `30 1 * * *` = 07:00 IST): `refresh
  --enrich-limit 25` → the above + RHP enrichment.
- Both: shared `site-v2-refresh` concurrency group; run the **audit gate**
  (`audit_v2_quality.py --gate`) before commit; commit `data/site` AND
  `data/site_v2`; upload raw snapshot artifacts. `refresh` is best-effort
  (per-step guarded) so a transient fetch failure can't block the commit — the
  audit gate is the authoritative guard.
- **Secrets required in GitHub** (degrade gracefully if absent): `KITE_*`,
  `DEEPSEEK_API_KEY`.

---

## 9. What was fixed in the 2026-05-24 session (regression history)

Four whole **classes** of regression were found by a systematic audit and fixed
in `normalize_v2/pipeline.py` + parsers:
1. **Silent slug-collision loss (~1,220 records)** — date-less per-issue records
   shared a `name_year:<name>:<fetch-year>` slug and overwrote each other on
   write. Fixed by `_assign_unique_slugs`.
2. **Impossible gains < −100% (~130 records)** — a source's pre-computed gain
   disagreed with the merged prices. Fixed by `_recompute_gains` (derive from
   canonical prices).
3. **`1+` name artifact (371 records)** — BSE `ScripName` literally `"1+ZEE
   MEDIA…"`. Fixed by `clean_company_name` + a strip in `normalize_name`
   (targeted `^\d+\+`; legit digit-names like 7NR/5paisa survive).
4. **Stale orphan files** — slug changes left old files on disk (disk > manifest).
   Fixed by `_prune_stale` (issues, quarantine, companies) + existing trajectory
   pruning.

Plus: NSE per-issue detail parser added; RHP zip-unwrap; `prospectus.schema.json`
created; the manifest⇄disk parity guard added to the audit; OFS/Buyback-aware
audit (no false `band_inverted`/`listed_no_listing_date`).

Verified end state: manifest == disk on all three trees; 0 null status; 0 true
duplicates (same normalized-name+type+year); 141 tests pass; gate passes.

**Residual long-tail audit findings (≈177 records, ~2.4%), all explained, NOT
bugs:** sub-₹1 bands on old fixed-price issues (14); issue_price_outside_band
(16, cut-off pricing); unusual_face_value (111, legit ₹2/₹5); Ruchi-Soya-style
2003-listing + 2022-FPO merges (a handful); current_price_nonpositive (2,
suspended); SME IPOs >1000× subscribed (6, real).

---

## 10. Known gaps, risks & follow-ups (open)

| # | Item | Severity | Note |
|---|---|---|---|
| 1 | **Not a git repository** | 🔴 high | `data/` is uncommitted and unversioned. No history, no rollback, no audit trail. `git init` + decide what to track (code yes; `data/raw` is 327 MB — consider LFS or R2). **Replicability depends on this.** |
| 2 | GitHub Actions secrets not set | 🔴 high | Kite/DeepSeek steps will fail until `KITE_*` + `DEEPSEEK_API_KEY` are added in repo settings. |
| 3 | Frontend still partly reads `data/site` (v1) | 🟡 | CI commits both. Once `web/` fully reads v2, drop `data/site` from the commit step and retire `normalize.py`/`site_builder.py`. |
| 4 | `raw_catalog` stale vs new per-issue endpoints | 🟡 | Drift logs ~1,450 benign "added" events. Run `orchestrator catalog` to rebaseline so drift detection is meaningful. |
| 5 | Sector classification not scheduled | 🟡 | New companies inherit no sector until `classify-sectors` is run. Schedule weekly if wanted. |
| 6 | `issue_size_paise` sparse (~16%) | 🟡 | Expected on the historical/NCD/OFS tail. For current IPOs prefer prospectus `hero.total_offer_paise`. Do NOT infer from narrative text (wrong > missing). |
| 7 | Trajectory freeze (`E.SUB.004`) unverified live | 🟡 | Freeze should fire 7 days post-close; confirm once cron runs. |
| 8 | `orchestrator/rhp_enrich.py` superseded | 🟢 low | Dead-ish framework; verify unused and delete. |
| 9 | `pytest` not in `.venv`; `pyotp` not in system python | 🟢 low | Test-running ergonomics; unify the env. |

---

## 11. Where everything is documented

- `docs/ASTRO_HANDOFF.md` — the frontend data contract (record shape, units,
  prospectus, indexes, gotchas).
- `docs/CI_GOLIVE.md` — the two workflows + audit gate + secrets.
- `docs/data/DATASET.md`, `SCHEMA_GUIDE.md`, `SOURCES.md` — dataset/schema/source references.
- `docs/data/EDGE_CASES.md`, `FUTURE_PROOFING.md`, `COVERAGE_AUDIT.md` — edge cases, future-proofing, source-coverage audit.
- `docs/data/SOURCE_PRECEDENCE.yaml`, `DEDUP_RULES.yaml` — precedence + dedup config.
- `docs/data/IPO_PAGE_SCHEMA.md` — the RHP page-schema contract the extractor fills.
- `docs/REFRESH_CYCLE.md`, `docs/OPERATIONS.md`, `docs/KITE.md` — ops.
- `docs/decisions/00*.md` — ADRs (canonical units, parallel v2 rebuild, academy spec).
- `HANDOFF.md` — older Claude-session handoff (design history; partly pre-v2).

---

## 12. How to run everything (commands)

```bash
# fetch fresh raw snapshots (network; writes data/raw + legacy data/site)
.venv/bin/python -m ipo_portal fetch --source all --allow-validation-errors

# rebuild canonical v2 tree from existing raw
.venv/bin/python -m ipo_portal.orchestrator normalize

# full go-forward cycle (the cron entrypoint)
.venv/bin/python -m ipo_portal.orchestrator refresh                 # full
.venv/bin/python -m ipo_portal.orchestrator refresh --skip-enrich   # hourly (no DeepSeek)

# RHP extraction for current IPOs (needs DEEPSEEK_API_KEY)
.venv/bin/python -m ipo_portal.orchestrator enrich-rhp --scan-pending --limit 25
.venv/bin/python scripts/extract_rich_rhp.py --url <RHP_URL> --slug <slug>   # single

# data-quality audit + CI gate
.venv/bin/python scripts/audit_v2_quality.py
.venv/bin/python scripts/audit_v2_quality.py --gate   # exit!=0 on structural corruption

# drift / catalog / sectors
.venv/bin/python -m ipo_portal.orchestrator drift
.venv/bin/python -m ipo_portal.orchestrator catalog
.venv/bin/python -m ipo_portal.orchestrator classify-sectors

# tests
python3 -m pytest -q --ignore=tests/test_kite_auth.py

# frontend smoke test (ingests data/site_v2)
cd web && node scripts/smoke-v2.mjs
```
