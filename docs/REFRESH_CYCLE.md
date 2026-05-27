# Refresh cycle & data freshness

How fresh each part of the dataset is, and how the refresh cadence keeps
it that way.

## The three cadences

| Mode | Command | Cadence | Steps | Use |
|---|---|---|---|---|
| **Subscriptions** | `refresh-subscriptions` | every 30 min during Indian market/public issue hours | NSE/BSE fetch → normalize → active V3 subscription delta | Keep live/current issue demand data fresh with minimal public churn |
| **Daily** | `refresh-daily --skip-enrich` | daily 07:00 IST | SEBI → NSE/BSE fetch → Kite → Yahoo Finance fallback → normalize → export-v3 → drift | Complete public V3 sweep + upstream drift check |
| **Filings** | `process-filings` | optional/budgeted batch | PDF cache/text extraction → DeepSeek/OpenRouter → citation verification → redaction | Add rich prospectus intelligence only when citation-safe |

The subscription mode may update raw/staging snapshots, but public V3 writes are
limited to active issue subscription artifacts, matching public index summary
fields, and trajectories. Trajectories include both category subscription
observations and NSE price-level `demand_curves` from the NSE Demand Data tab
when available. Consolidated all-exchange subscription books are published in
`subscription.consolidated` when the BSE consolidated bid-detail endpoint has
data; NSE/BSE exchange-specific books remain in `subscription.by_exchange`.
Daily mode does the deterministic full V3 export.

## Per-source freshness contract

From `docs/data/SOURCES.md`, with the staleness tolerance that trips a
`degraded` flag in the manifest:

| Source | Refresh | Staleness tolerance | Tier |
|---|---|---|---|
| SEBI filings | hot (30 min) | 24h | primary (filing signal) |
| NSE current/upcoming | hot (30 min) | 2h | primary |
| NSE subscription (bid-book) | hot (30 min) | 2h | primary |
| NSE/BSE document feeds | daily | 24h | primary |
| BSE performance pages | daily | 7d | primary |
| Moneycontrol | daily | 7d | enrichment |
| RHP extraction | on new filing | n/a (one-shot per issue) | primary |

## What each step refreshes

1. **SEBI scrape** → `data/raw/sebi/public_issue_filings/` — newest DRHP
   filings. Earliest IPO signal (precedes NSE/BSE).
2. **NSE/BSE fetch** → `data/raw/{nse,bse}/…` — current issues, document
   indexes, performance, bid-book. (The existing v1 fetch.)
3. **Kite/Yahoo prices** → `data/raw/{kite,yahoo}/performance/` — Kite is
   preferred when credentials exist; Yahoo Finance is the public interim
   fallback for listing/current price calculations.
4. **normalize** → `data/site_v2/` staging — rebuilds every issue record,
   index, company, and trajectory from raw.
5. **export-v3** → `data/ipo_watch_v3/` — materializes the public
   self-contained contract. Existing pass/review prospectus facts are
   preserved across export.
6. **process-filings** → `data/ipo_watch_v3/issues/<slug>/prospectus_facts.json`
   — optional citation-verified filing intelligence batch. Never publishes
   unsupported scalar facts.
7. **audit-source-structure** → `data/reports/primary_source_structure_audit.json`
   — reports primary NSE/BSE/SEBI parser/fetch gaps. Run with `--gate` before
   committing a scheduled public-data refresh.
8. **drift** → `data/reports/upstream_drift.jsonl` — flags upstream
   schema changes.

## Freshness signals in the data

* `data/ipo_watch_v3/manifest.json` → `generated_at`, `dataset_version`
  (date-stamped per build).
* Each record → `generated_at` + `sources[].snapshot_at` (when the
  underlying snapshot was fetched).
* `data/reports/refresh_runs.jsonl` → per-run, per-step status + timing.

A consumer can compute staleness as `now - sources[].snapshot_at` per
source and decide whether to trust a field.

## Trajectory freeze

Subscription trajectories freeze 7 days after the issue closes
(`frozen_at` in the trajectory file). After freezing, the trajectory is
not re-accreted even if a stale bid-book snapshot reappears
(`EDGE_CASES.md` E.SUB.004). This keeps historical subscription curves
stable.

## RHP extraction cadence

V3 filing intelligence is explicit and budgeted:

```bash
.venv/bin/python -m ipo_portal.orchestrator process-filings \
  --limit 10 \
  --provider deepseek \
  --text-extractor liteparse \
  --quality-gate
```

PDFs/text/model calls are cached. Usage and cost are logged under
`data/reports/deepseek_usage.jsonl` or `data/reports/openrouter_usage.jsonl`.

## Cron setup

```cron
# Subscription cycle — every 30 min during market/public issue hours (Mon-Fri)
*/30 9-16 * * 1-5  cd /path/to/IPO && .venv/bin/python -m ipo_portal.orchestrator refresh-subscriptions >> data/reports/cron_subscriptions.log 2>&1

# Daily V3 cycle
0 7 * * *  cd /path/to/IPO && .venv/bin/python -m ipo_portal.orchestrator refresh-daily --skip-enrich >> data/reports/cron_daily.log 2>&1
```

GitHub Actions equivalents:

- `.github/workflows/refresh-daily.yml`
- `.github/workflows/refresh-subscriptions.yml`

Manual source-coverage audit:

```bash
.venv/bin/python -m ipo_portal.orchestrator audit-source-structure --gate
```

## Degraded-build and last-good handling

V3 now carries freshness state directly in `data/ipo_watch_v3/manifest.json`:

- `degraded`: true when a required source is missing/stale, or when neither
  Kite nor Yahoo has a fresh market snapshot.
- `stale_sources[]`: machine-readable source rows with `source`, `state`,
  `latest_fetched_at`, `age_hours`, and `staleness_tolerance_hours`.
- `source_freshness`: per-source freshness map for SEBI, NSE, BSE, Yahoo, and
  Kite.

Fetch failures are logged to `data/reports/source_fetch_events.jsonl` and do
not write empty latest raw snapshots. The previous latest raw snapshot remains
the stale-if-fail input for normalization.

`refresh-daily` snapshots the current non-degraded V3 tree to
`data/.last_good/ipo_watch_v3` before running. If a guarded refresh step fails
after that point, it restores the last-good tree and writes diagnostics to
`data/reports/refresh_runs.jsonl` and
`data/reports/latest_refresh_summary.json`. GitHub/Cloudflare jobs should commit
or deploy only when the command exits 0 and the manifest is not unsafe for the
target consumer. A degraded manifest can be served deliberately as a stale
public build, but a failed refresh should publish diagnostics only.

## GitHub/Cloudflare operating decision

GitHub Actions is adequate for the static site build and artifact publication
when endpoints are healthy, but it is not a complete reliability boundary for
Indian exchange/SEBI network variability. The safer production shape is:

1. Run the scheduled fetch/normalize/export job on GitHub Actions initially.
2. Keep the last-good restore logic enabled and make commit/push conditional on
   successful gates.
3. Move only the data refresh to a small scheduled VM/worker if SEBI/NSE/BSE DNS
   or session behavior remains flaky in Actions; that worker should commit the
   self-contained `data/ipo_watch_v3` artifact back to Git.
4. Let Cloudflare build only from committed V3 artifacts, so rollback is a Git
   revert or redeploy of the previous commit, not a live scraper rerun.
