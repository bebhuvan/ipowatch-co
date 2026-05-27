# IPO Watch V3 Current Status

Date: 2026-05-27
Workspace: `/home/bhuvanesh.r/Documents/Bhuvan projects/IPO`

## Current Operational Status As Of 2026-05-27

The project now has a private GitHub baseline and active GitHub Actions
workflows. The repository is:

```txt
https://github.com/bebhuvan/ipowatch-co
```

Repository facts verified locally with `gh`:

- Owner/repo: `bebhuvan/ipowatch-co`
- Visibility: private
- Default branch: `main`
- Current pushed baseline: `fb67d9a1 Initialize IPO Watch site pipelines`
- Local branch tracks: `origin/main`
- Actions default workflow token permission: `write`

The private repo contains the application code, docs, workflows, and the
publishable V3 dataset. It deliberately does not track local secrets or
high-churn/intermediate working trees:

- `.env`
- `.venv/`
- `data/raw/`
- `data/cache/`
- `data/processed/`
- `data/reports/`
- `data/.last_good/`
- `data/private/`
- `data/site/`
- `data/site_v2/`
- `web/node_modules/`, `web/dist/`, and Astro build cache via `web/.gitignore`

Important: the repo baseline has been committed and pushed. Older notes in this
file that described the workspace as wholly untracked are obsolete.

## GitHub Actions Status

Active workflows:

| Workflow | File | Purpose | Trigger |
|---|---|---|---|
| Deploy IPOWatch Worker | `.github/workflows/deploy-worker.yml` | Manual Cloudflare Worker build/deploy from the committed V3 dataset | `workflow_dispatch` |
| Refresh IPOWatch V3 daily | `.github/workflows/refresh-daily.yml` | NSE/BSE fetch, Kite/Yahoo market data, Tijori enrichment, normalize, export V3, gates, build, deploy, commit refreshed dataset | Daily `01:30 UTC` / `07:00 IST`, manual |
| Refresh SEBI new filings | `.github/workflows/refresh-new-filings.yml` | Fetch SEBI filing PDFs, extract/generate article, build, deploy, commit new filing article data | Weekdays `13:30 UTC` / `19:00 IST`, manual |
| Refresh IPOWatch V3 subscriptions | `.github/workflows/refresh-subscriptions.yml` | Active subscription refresh, V3 subscription modules/trajectories, build, deploy, commit subscription deltas | Weekdays every 30 minutes from `04:00-11:30 UTC`, manual |

The daily workflow now includes Tijori:

- `ipo_portal.orchestrator.refresh.run_refresh(..., skip_tijori=False)` fetches
  `https://b2b.tijorifinance.com/b2b/v1/in/api/kite-screener/ipo/` before
  normalization.
- It writes `data/derived/tijori_ipo_enrichment.json`.
- It writes `data/derived/sector_map.json`.
- `refresh` and `refresh-daily` expose `--skip-tijori` for emergency bypass.
- `.github/workflows/refresh-daily.yml` commits `data/derived` with the V3
  dataset and uploads it as diagnostics.

Secrets currently present in GitHub Actions:

- `DEEPSEEK_API_KEY`
- `GEMINI_API_KEY`
- `INDIA_DATAHUB_API_KEY`
- `KITE_API_KEY`
- `KITE_API_SECRET`
- `OPENROUTER_API_KEY`

Secrets still missing when last checked with `gh secret list` on 2026-05-27:

- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_ACCOUNT_ID`
- `KITE_USER_ID`
- `KITE_PASSWORD`
- `KITE_TOTP_SECRET`

Effect of missing secrets:

- Cloudflare deploy steps will fail until both Cloudflare secrets exist.
- Kite API key/secret are present, but unattended Kite auto-login cannot work
  until Kite user ID, password, and TOTP seed exist.
- The daily refresh is coded to skip Kite gracefully if a valid session cannot
  be created, and to use Yahoo fallback market data where available.

If the missing secrets were added after this check, re-run:

```bash
gh secret list --repo bebhuvan/ipowatch-co
```

Do not print secret values.

## Canonical Dataset Location

Canonical V3 public artifact:

```txt
data/ipo_watch_v3/
```

Compatibility alias retained for older code/docs during transition:

```txt
data/site_v3 -> data/ipo_watch_v3
```

The Astro data loader now defaults to `data/ipo_watch_v3`; `IPO_DATA_DIR` can still override the root.

## Linked Context Map

Use this file as the starting point, then follow these links for the detailed
contract and operating model:

- Public V3 data contract: [V3_CONTRACT.md](V3_CONTRACT.md)
- Latest rebuild / launch audit: [AUDIT_FINDINGS.md](AUDIT_FINDINGS.md)
- Refresh cadence and freshness model: [REFRESH_CYCLE.md](REFRESH_CYCLE.md)
- Source surface audit for NSE/BSE/SEBI: [NSE_BSE_PRIMARY_MARKET_COVERAGE_AUDIT.md](NSE_BSE_PRIMARY_MARKET_COVERAGE_AUDIT.md)
- DeepSeek / filing processor notes: [DEEPSEEK_FILING_PROCESSOR.md](DEEPSEEK_FILING_PROCESSOR.md)
- Rebuild report: [V3_REBUILD_REPORT.md](V3_REBUILD_REPORT.md)
- Target rich IPO page sample: [samples/a-g-universal-drhp-sample.md](samples/a-g-universal-drhp-sample.md)
- Core V3 exporter: [ipo_portal/site_v3/export.py](../ipo_portal/site_v3/export.py)
- Refresh orchestrator: [ipo_portal/orchestrator/refresh.py](../ipo_portal/orchestrator/refresh.py)
- CLI entrypoint: [ipo_portal/orchestrator/cli.py](../ipo_portal/orchestrator/cli.py)
- Filing intelligence processor: [ipo_portal/filing_processor.py](../ipo_portal/filing_processor.py)
- Astro V3 loader: [web/src/lib/ipodata.ts](../web/src/lib/ipodata.ts)
- Daily GitHub workflow: [../.github/workflows/refresh-daily.yml](../.github/workflows/refresh-daily.yml)
- Subscription GitHub workflow: [../.github/workflows/refresh-subscriptions.yml](../.github/workflows/refresh-subscriptions.yml)
- New filings GitHub workflow: [../.github/workflows/refresh-new-filings.yml](../.github/workflows/refresh-new-filings.yml)
- Manual deploy workflow: [../.github/workflows/deploy-worker.yml](../.github/workflows/deploy-worker.yml)

## New Codex Session Brief

Start from the GitHub-backed state, not from a local-only prototype. IPOWatch is
now a private repository with an initial committed V3 dataset and scheduled
workflows. The remaining work is to prove the scheduled runs end-to-end in
GitHub Actions and harden the failure behavior.

Product scope remains: SEBI DRHP/RHP filing discovery, new filing article
generation, upcoming/open/closed/listed IPO lifecycle, live subscription
tracking, listing performance, historical analytics, and nearby primary-market
products in their own sections: FPOs, rights issues, buybacks, OFS, QIPs,
InvITs, REITs, public debt/NCDs, call-money/partly-paid flows, and other
exchange-published public issue actions. Equity IPO/FPO pages take precedence
under `/ipos/`; non-equity and secondary/other instruments route to their own
sections such as `/ofs/`, `/buybacks/`, `/rights/`, `/debt/`, `/reits/`,
`/invits/`, `/call-money/`, and `/public-issues/`.

The next session should verify the live automation path, not just inspect code:

1. Confirm the newly added GitHub secrets actually appear in
   `gh secret list --repo bebhuvan/ipowatch-co`.
2. Trigger `Deploy IPOWatch Worker` for production only after Cloudflare secrets
   are present.
3. Trigger `Refresh SEBI new filings` once manually to confirm Gemini PDF/article
   generation works inside Actions.
4. Trigger `Refresh IPOWatch V3 daily` once manually to confirm NSE/BSE, Tijori,
   Yahoo/Kite behavior, quality gates, Astro build, Cloudflare deploy, and data
   commit all work in Actions.
5. Trigger `Refresh IPOWatch V3 subscriptions` during a market/public-issue
   window to confirm subscription delta generation and commit behavior.

Failure policy: wrong public data is worse than missing data. If a fetch,
normalization, audit, build, or deploy step fails, the workflow should not commit
or publish a bad public V3 tree. Preserve the last good build and commit
diagnostics only. Any future hardening should make degraded/stale source state
visible in `manifest.json`, run summaries, and public UI where appropriate.

## Dataset Snapshot

From `data/ipo_watch_v3/manifest.json` after the latest committed local export:

| Metric | Count |
|---|---:|
| Public issues | 5,200 |
| Companies | 4,355 |
| Subscription trajectories | 223 |
| Performance rows | 2,488 |
| Quarantined / removed from public indexes | 1,891 |
| Review-tier public issues | 2,010 |
| Dataset version | `v3.2026.05.26-0936` |
| Schema version | `3.0.0` |
| Manifest degraded flag | `true` |

The manifest was updated to identify `data/ipo_watch_v3` as the root and `data/site_v3` as a compatibility alias.

Current source freshness in the manifest is mixed:

- Fresh: `capitalmarket`, `moneycontrol`, `prime`, `trendlyne`
- Stale: `bse`, `nse`, `sebi`, `yahoo`, `kite`
- Synthetic stale/missing source: `market_data`

This degraded flag is expected until the scheduled Action runs successfully with
fresh source snapshots and deploy credentials.

New filing article dataset:

- `data/ipo_watch_v3/new_filings/m-k-sons-fine-jewels-20260520-d6ac2d77/article.json`
- This article is acceptable as a first pipeline artifact but still needs
  editorial/product refinements before treating the generated article format as
  final.

## Validation State

Current gates and checks:

```bash
.venv/bin/python scripts/audit_v3_quality.py --site-root data/ipo_watch_v3 --gate
```

Result: pass, no findings.

```bash
.venv/bin/python scripts/audit_prospectus_facts.py --site-root data/ipo_watch_v3 --gate
```

Result:

```json
{"total": 3, "pass": 2, "review": 1, "fail": 0}
```

Strict prospectus gate intentionally fails because one real extraction is `review`:

```bash
.venv/bin/python scripts/audit_prospectus_facts.py --site-root data/ipo_watch_v3 --gate --strict
```

## Current Launch Caveats

The private repo baseline exists and Actions are active, but the live system is
not fully proven until the missing secrets are visible and at least one manual
run of each workflow succeeds.

Known caveats:

- Cloudflare deploy cannot succeed until `CLOUDFLARE_API_TOKEN` and
  `CLOUDFLARE_ACCOUNT_ID` are present in GitHub Actions secrets.
- Kite unattended login cannot succeed until `KITE_USER_ID`, `KITE_PASSWORD`,
  and `KITE_TOTP_SECRET` are present. Until then, the daily pipeline should skip
  Kite and use Yahoo fallback where possible.
- The committed manifest is currently `degraded: true` because several source
  snapshots are stale relative to the freshness thresholds.
- The latest local Wrangler dry run was not re-run successfully because local
  Node is `20.20.2`, while Wrangler requires Node `>=22`. GitHub Actions uses
  Node 22, so this is a local workstation limitation, not an Actions design
  issue.
- The Actions have not yet been manually triggered after the private repo push.
  Do not assume Cloudflare deploy or scheduled data commits work until a real
  Action run proves it.
- Review `data/reports/upstream_drift.jsonl` and source coverage diagnostics
  after the first successful Action refresh before declaring the data pipeline
  autonomous.

## V3 Exporter And Contract

Implemented:

- Raw-first V3 exporter: `ipo_portal/site_v3/export.py`
- Default export root changed to `data/ipo_watch_v3`
- Canonical split modules:
  - `issues/<slug>/core.json`
  - `market.json`
  - `subscription.json`
  - `filings.json`
  - `prospectus_facts.json`
  - `provenance.json`
- Schemas:
  - `docs/schema/v3/*.schema.json`
  - copied public schemas under `data/ipo_watch_v3/_meta/schemas/`
- Public indexes exclude quarantined records.
- V2 comparison report exists under `data/ipo_watch_v3/_meta/v2_comparison.json`.
- Source coverage report exists under `data/ipo_watch_v3/_meta/source_coverage.json`.

`export-v3` now preserves existing citation-verified `prospectus_facts.json`
outputs across rebuilds, including review-state extractions. Placeholder
`not_extracted` documents are regenerated from the current issue/document state.

## Website State

Astro site reads V3 directly through:

```txt
web/src/lib/ipodata.ts
```

Current fallback root:

```txt
data/ipo_watch_v3
```

Runtime dependency on `data/site_v2`: none for site data consumption.

Implemented in this pass:

- `web/src/lib/ipodata.ts` reads `issues/<slug>/prospectus_facts.json`.
- Issue detail pages include extracted prospectus pages even when they are not
  in the current/recent issue set.
- Issue detail pages render citation-backed business snapshot, industry/macro
  facts, financial highlights, risks/red flags, advisors, valuation, governance,
  source pages, and extraction quality state.

Still needed:

- More chart-ready financial table rendering.
- A stricter default policy decision for whether review-state extraction pages
  should be indexed or noindexed.

Sample Markdown page for target UX/content shape:

```txt
docs/samples/a-g-universal-drhp-sample.md
```

## Filing Intelligence Pipeline

Implemented:

- `ipo_portal/filing_processor.py`
- CLI command:

```bash
.venv/bin/python -m ipo_portal.orchestrator process-filings-v3
```

Capabilities:

- Downloads direct PDFs or ZIP-wrapped PDFs.
- Caches PDFs under `data/cache/primary_filings/`.
- Supports local text extraction:
  - `pdftotext`
  - `liteparse`
- Caches extracted text under `data/cache/pdf_text/<backend>/`.
- Adds page markers into model input so DeepSeek can cite pages accurately.
- Verifies every non-null scalar fact against the cited page.
- Repairs wrong page numbers when the exact excerpt appears elsewhere.
- Redacts unsupported citations.
- Redacts unshaped primitive facts before publication.
- Scores extraction quality as `pass`, `review`, or `fail`.
- Can gate batch runs with:
  - `--quality-gate`
- `--strict-quality-gate`

Alias:

```bash
.venv/bin/python -m ipo_portal.orchestrator process-filings
```

Optional extractor dependencies:

```bash
.venv/bin/python -m pip install -r requirements-extractors.txt
```

## Real DeepSeek Extractions Completed

All are under `data/ipo_watch_v3/issues/<slug>/prospectus_facts.json`.

| Slug | State | Verified facts | Redaction rate | Repair rate | Missing sections | Cost |
|---|---|---:|---:|---:|---|---:|
| `a-g-universal-c13e20` | pass | 120 | 0.1045 | 0.0 | none | `$0.033814` |
| `a-b-infrabuild-c81c90` | pass | 137 | 0.1646 | 0.0488 | none | `$0.035846` |
| `20-microns-nano-minerals-f0cd70` | review | 108 | 0.2174 | 0.1304 | `valuation_and_peers` | `$0.031415` |

Quality interpretation:

- Normal gate passes because there are no `fail` extractions.
- Strict gate fails because `20-microns` needs human/model review.
- Costs are low enough to justify controlled batch expansion.

## PDF / Model Experiments

DeepSeek:

- Text-only chat endpoint.
- Works well with LiteParse/pdftotext slices.
- Current production path uses DeepSeek for structured JSON extraction.

OpenRouter / Gemini:

- `google/gemini-2.5-flash` accepted a full PDF natively.
- Full-PDF business-section test on Manipal Health DRHP:
  - Prompt tokens: 189,222
  - Completion tokens: 10,117
  - Estimated cost: about `$0.082`
  - Citation pass rate was only 71.67%, so not publish-ready.
- `google/gemini-3.1-flash-lite` failed on full PDF through OpenRouter with provider 400.

Qwen VL:

- `qwen/qwen3-vl-30b-a3b-instruct` rejected PDF file input through the tested OpenRouter route.
- It likely needs rasterized page images rather than PDF file parts.

Local PDF extraction:

- `pdftotext`: fastest, stable.
- `LiteParse`: slower but page-aware and good enough for verifier/model slices.
- `MarkItDown`: OK on page-scoped PDFs, not reliable for full DRHP default.

Comparison harness:

```txt
scripts/compare_pdf_extractors.py
```

## New / Changed Files Worth Knowing

Core V3:

- `ipo_portal/site_v3/export.py`
- `ipo_portal/filing_processor.py`
- `ipo_portal/openrouter.py`
- `ipo_portal/orchestrator/cli.py`
- `ipo_portal/orchestrator/refresh.py`

Audit / scripts:

- `scripts/audit_v3_quality.py`
- `scripts/audit_prospectus_facts.py`
- `scripts/compare_pdf_extractors.py`

Tests:

- `tests/test_filing_processor.py`
- `tests/test_openrouter_client.py`
- `tests/test_site_v3_export.py`

Docs:

- `docs/V3_CONTRACT.md`
- `docs/V3_REBUILD_REPORT.md`
- `docs/DEEPSEEK_FILING_PROCESSOR.md`
- `docs/samples/a-g-universal-drhp-sample.md`
- this file

## Refresh Commands

Daily full refresh:

```bash
.venv/bin/python -m ipo_portal.orchestrator refresh-daily
.venv/bin/python scripts/audit_v3_quality.py --site-root data/ipo_watch_v3 --gate
.venv/bin/python scripts/audit_prospectus_facts.py --site-root data/ipo_watch_v3 --gate
.venv/bin/python -m ipo_portal.orchestrator audit-source-structure --gate
```

For scheduled/public-data refreshes, use `refresh-daily --skip-enrich` and run
filing intelligence separately with `process-filings`. This keeps model/API
costs and long PDF extraction batches out of the deterministic daily data build.
Daily refresh now writes a Yahoo Finance performance snapshot before
normalization, so V3 current-price and gain calculations continue to populate
while Kite credentials are unavailable. If Kite is configured, Kite remains the
higher-precedence source for `listing_performance.current_price_paise`.

High-frequency subscription refresh:

```bash
.venv/bin/python -m ipo_portal.orchestrator refresh-subscriptions
```

The subscription command fetches/normalizes fresh source snapshots, then limits
public V3 writes to active issue subscription modules, matching index summary
fields, and trajectories. Use `--skip-fetch` to test from existing raw snapshots.
Trajectory files now carry NSE price-level `demand_curves` when the NSE
Demand Data endpoint is available, alongside category-wise subscription
observations. BSE demand schedules are also preserved as category-wise bid
observations when BSE publishes them.

Primary source-structure audit:

```bash
.venv/bin/python -m ipo_portal.orchestrator audit-source-structure
```

Current audit output reports the source coverage inventory. With NSE LWF,
G-Sec/non-competitive bidding, MFSS, and discontinued SGB explicitly out of
scope, the primary source-structure gate currently passes with zero blocking
primary gaps. The remaining parser-failed rows in raw source coverage are BSE
HTML demand graph companions; structured bid/demand APIs are the canonical data
source.

Scheduled Git refresh:

- `.github/workflows/refresh-daily.yml`: once daily at 01:30 UTC / 07:00 IST.
- `.github/workflows/refresh-subscriptions.yml`: every 30 minutes during the
  Indian market/public-issue window on weekdays.
- `.github/workflows/refresh-new-filings.yml`: weekdays at 13:30 UTC / 19:00
  IST for SEBI new filing PDF/article generation.
- `.github/workflows/deploy-worker.yml`: manual deploy for preview or
  production.

## Automation Design Checklist For Next Session

The next implementation pass should turn the current cron-ready scripts into a
resilient publish pipeline. Work through these one by one:

1. Source fetch resilience: add bounded retries, per-source timeout budgets,
   and source-specific stale-if-fail behavior. SEBI, NSE, BSE, and Yahoo/Kite
   failures should be visible in the manifest and reports.
2. Last-good-build protection: if a required gate fails, do not commit the new
   public `data/ipo_watch_v3` tree. Preserve the previous good dataset and
   publish diagnostics only.
3. Staleness contract: implement `manifest.degraded`, `stale_sources[]`, and
   source freshness thresholds from [REFRESH_CYCLE.md](REFRESH_CYCLE.md).
4. Status graduation: explicitly test transitions from filed/coming-soon to
   upcoming, open, closed, listed, and historical using SEBI filing signals,
   exchange date announcements, current issue feeds, past issue feeds, and
   listing/performance data.
5. Subscription automation: confirm active issue discovery, NSE issue-detail
   fetches, NSE consolidated/demand tabs, BSE bid-detail endpoints, trajectory
   updates, and freeze-after-close behavior for each active product type.
6. Filing automation: monitor SEBI DRHP/RHP feeds, create/update the issue
   shell, cache PDFs, extract text/tables, run model batches only when budget and
   credentials exist, and publish only citation-verified facts.
7. Market automation: use Kite when configured, Yahoo Finance as interim
   fallback, and record symbol-match confidence. Missing prices should not block
   non-market issue pages.
8. Parser coverage: review `source_coverage.json`,
   `primary_source_structure_audit.json`, and `upstream_drift.jsonl` after each
   fetch. Add parsers or explicit product-scope exclusions for every recurring
   primary-market endpoint.
9. Git/Cloudflare publishing: decide whether GitHub Actions is enough or whether
   a small scheduled worker/VM should fetch data and commit to Git. The answer
   depends on rate limits, endpoint stability, runtime length, artifact size,
   and deploy failure recovery.
10. Observability: write concise machine-readable run summaries, emit changed
    counts, list skipped/degraded sources, and make failures actionable without
    inspecting raw logs.

Filing intelligence batch:

```bash
.venv/bin/python -m ipo_portal.orchestrator process-filings \
  --limit 10 \
  --provider deepseek \
  --text-extractor liteparse \
  --quality-gate
```

Batch discovery skips filings that already have a current pass/review extraction
for the same document URL. Use `--force` for deliberate reprocessing.

Model/API environment variables:

- `DEEPSEEK_API_KEY` for DeepSeek extraction.
- `OPENROUTER_API_KEY` for OpenRouter model experiments.
- Kite variables are optional for market prices: `KITE_API_KEY`,
  `KITE_API_SECRET`, `KITE_USER_ID`, `KITE_PASSWORD`, `KITE_TOTP_SECRET`.

Cost/token logs:

- DeepSeek: `data/reports/deepseek_usage.jsonl`.
- OpenRouter: `data/reports/openrouter_usage.jsonl`.

## Verification Commands

Use `.venv/bin/python` unless a command specifically requires system Python.

Current focused checks:

```bash
.venv/bin/python -m py_compile \
  ipo_portal/filing_processor.py \
  ipo_portal/orchestrator/cli.py \
  ipo_portal/site_v3/export.py \
  scripts/audit_v3_quality.py \
  scripts/audit_prospectus_facts.py \
  scripts/compare_pdf_extractors.py

python3 -m pytest -q tests/test_filing_processor.py tests/test_openrouter_client.py

.venv/bin/python scripts/audit_v3_quality.py --site-root data/ipo_watch_v3 --gate
.venv/bin/python scripts/audit_prospectus_facts.py --site-root data/ipo_watch_v3 --gate
```

Full acceptance checks rerun on 2026-05-26:

```bash
.venv/bin/python -m ipo_portal.orchestrator export-v3
.venv/bin/python scripts/audit_v3_quality.py --site-root data/ipo_watch_v3 --gate
.venv/bin/python scripts/audit_prospectus_facts.py --site-root data/ipo_watch_v3 --gate
.venv/bin/python -m ipo_portal.orchestrator audit-source-structure --gate
python3 -m pytest -q --ignore=tests/test_kite_auth.py
cd web && npm run build
```

Results from the 2026-05-26 full acceptance pass:

- `refresh-daily --skip-enrich`: partial pass. NSE/BSE fetch, Yahoo fallback pricing,
  normalize, export, and drift scan completed; SEBI failed due local DNS resolution
  for `www.sebi.gov.in`.
- `export-v3`: passed inside the refresh, 5,202 public issues, 4,356 companies, 220 trajectories.
- V3 quality gate: passed, no findings.
- Prospectus facts gate: passed with `2 pass`, `1 review`, `0 fail`.
- Source structure gate: passed with 0 blocking parser gaps.
- Python tests: `164 passed`.
- Astro build: passed, 1,387 pages built from `data/ipo_watch_v3`.

Additional repo/pipeline setup checks rerun on 2026-05-27:

```bash
.venv/bin/python -m py_compile \
  ipo_portal/orchestrator/refresh.py \
  ipo_portal/orchestrator/cli.py \
  ipo_portal/new_filings.py \
  ipo_portal/tijori.py

.venv/bin/python -m ipo_portal.orchestrator refresh-daily --help
.venv/bin/python scripts/update_tijori_enrichment.py
cd web && npm run build
```

Results from the 2026-05-27 setup pass:

- Python compile checks: passed.
- `refresh-daily --help`: includes `--skip-tijori` and `--skip-enrich`.
- Tijori feed update: passed with 547 rows, 547 ISINs, 543 financial profiles,
  and 547 sector-map entries.
- Astro build: passed, 2,093 static pages generated.
- GitHub repo creation/push: passed, private repo `bebhuvan/ipowatch-co`,
  commit `fb67d9a1`.
- GitHub workflow token permission: set to `write`.
- GitHub workflows: all four expected workflows are active.
- Local Wrangler dry run: not validated locally because local Node is 20.20.2;
  workflows use Node 22.

Additional demand-data verification:

- `export-v3` now writes 223 trajectory files in the current committed dataset.
- `bio-medica-laboratories-d385c5` and `q-line-biotech-7c0bb8` include NSE
  `demand_curves` from the raw `demand_data_nse_*` / `demand_data_all_*`
  snapshots currently on disk.

## What Is Left

Priority 1: filing intelligence at scale.

- Run controlled batches of 10-25 filings.
- Use `--quality-gate`.
- Review `review` files before rendering publicly.
- Improve section locator aliases for valuation/peer sections, offer objects, promoter tables, and debt/NCD filings.
- Improve financial table schema and chart-ready extraction.

Priority 2: rich site rendering.

- Add financial trend charts from verified financial rows.
- Add noindex/manual-review handling if review-state prospectus pages should not
  be publicly indexed.
- Improve dense citation UX for long risk and governance sections.

Priority 3: analytics and source coverage.

- Validate Yahoo/Kite symbol mappings and market confidence.
- Finish unsupported/parser-failed endpoint triage in `source_coverage.json`.
- Re-run V2 comparison after filing intelligence expansion.

## Do Not Forget

- Never print `.env` or secrets.
- OpenRouter key is read from `.env`; do not expose it.
- DeepSeek/OpenRouter responses are cached and usage is logged.
- Wrong data is worse than missing data.
- Public rich pages should use only `quality.state == "pass"` by default, or visibly label `review`.
