# Operations playbook

How to run, refresh, and monitor the IPO Watch v2 data pipeline.

## TL;DR

```bash
# Full daily cycle (cron entrypoint): SEBI + NSE/BSE → normalize → enrich → drift
python -m ipo_portal.orchestrator refresh

# Fast intraday cycle (SEBI + normalize + current-IPO RHP extraction)
python -m ipo_portal.orchestrator refresh --hot

# Rebuild data/site_v2/ from existing raw snapshots (no network)
python -m ipo_portal.orchestrator normalize
```

## The pipeline at a glance

```
                    ┌─────────────── raw snapshots (immutable) ───────────────┐
  NSE / BSE  ──────▶│ data/raw/nse/…  data/raw/bse/…                          │
  SEBI       ──────▶│ data/raw/sebi/public_issue_filings/…                    │
  Moneycontrol ────▶│ data/raw/moneycontrol/…                                 │
                    └──────────────────────────┬─────────────────────────────┘
                                                │  normalize (parsers + precedence + consolidation)
                                                ▼
                    ┌──────────── data/site_v2/ (canonical, validated) ───────┐
                    │ issues/by-slug/<slug>.json     ← full records           │
                    │ issues/index.json, by-year/, by-status/, by-kind/       │
                    │ companies/index.json, by-slug/                          │
                    │ issues/<slug>/prospectus.json  ← RHP extraction         │
                    │ quarantine/<slug>.json         ← failed validation      │
                    │ manifest.json                  ← build state + counts   │
                    └─────────────────────────────────────────────────────────┘
                                                │  consumed by
                                                ▼
                                      ipowatch.co (Astro)
```

## Commands

| Command | What it does | Network | Cost |
|---|---|---|---|
| `refresh` | Full cycle (all steps below) | yes | ~$0.15/new RHP |
| `refresh --hot` | SEBI + normalize + current-IPO enrichment | yes | ~$0.15/new RHP |
| `refresh --skip-fetch` | Everything except NSE/BSE fetch | partial | — |
| `normalize` | Rebuild `data/site_v2/` from raw | no | free |
| `catalog` | Re-run Phase 1 endpoint catalog (DeepSeek) | no* | ~$0.60 |
| `schema` | Re-synthesize canonical JSON Schemas (DeepSeek) | no* | ~$0.03 |
| `audit` | Pollution audit on existing site data (DeepSeek) | no* | ~$0.05 |
| `gap-scan` | Report fields the catalog lists but records lack | no | free |
| `drift` | Detect upstream schema changes vs catalog | no | free |
| `enrich-rhp --url U --slug S` | Extract one RHP | downloads PDF | ~$0.15 |
| `enrich-rhp --scan-pending` | Extract RHPs for current IPOs only | downloads PDFs | ~$0.15 each |

\* DeepSeek calls hit the API but are disk-cached, so reruns are free.

## Recommended cron cadence

| Cadence | Command | Why |
|---|---|---|
| Every 30 min (market hours) | `refresh --hot` | Catch new SEBI filings + open-IPO subscription; extract any new RHP |
| Daily 02:00 IST | `refresh` | Full NSE/BSE sweep + drift detection |
| Weekly | `drift` (alerting) | Surface upstream shape changes for review |
| On schema edit | `normalize` | Rebuild after any parser/schema change |

Example crontab:

```cron
*/30 9-16 * * 1-5  cd /path/to/IPO && .venv/bin/python -m ipo_portal.orchestrator refresh --hot
0 2 * * *          cd /path/to/IPO && .venv/bin/python -m ipo_portal.orchestrator refresh
```

The GitHub Actions hourly job (already in `.github/`) should call
`refresh` after the existing fetch step.

## Idempotency & safety

* **Raw snapshots are immutable** — never edited, only appended.
* **All writes are hash-gated** — re-running with no upstream change is a
  git no-op.
* **DeepSeek calls are disk-cached** by input hash (`data/cache/deepseek/`,
  `data/cache/rhp_extract/`) — reruns cost nothing.
* **Each refresh step is independently guarded** — one failing (e.g.,
  NSE 429) doesn't abort the others.
* **No backfill** — `enrich-rhp --scan-pending` only touches current
  issues (Open/Upcoming, recently Listed, recently Filed). The ~1,600
  historical document-only records are never swept.

## Monitoring

| Signal | Where | Action |
|---|---|---|
| Build counts | `data/site_v2/manifest.json` | Compare issues_published vs prior |
| Quarantined records | `manifest.issues_quarantined` + `data/site_v2/quarantine/` | Investigate validation failures |
| Schema failures | `manifest.schema_failures` | Should be 0; non-zero = schema/parser drift |
| Upstream drift | `data/reports/upstream_drift.jsonl` | New/removed/changed upstream fields |
| Refresh history | `data/reports/refresh_runs.jsonl` | Per-step status + timing |
| DeepSeek spend | `data/reports/deepseek_usage.jsonl` | Token + cost audit |
| Fetch failures | `data/reports/fetch_failures.jsonl` | Source outages |

## When something breaks

* **NSE returns 429** — back off; the warm-up + throttle usually
  recovers next run. Not fatal; `refresh` continues.
* **A record is quarantined** — open `data/site_v2/quarantine/<slug>.json`,
  read `data_quality.errors`. Either fix the parser or the schema.
* **Drift detected (removed field)** — `drift` exits non-zero. Re-run
  `catalog --only <endpoint>` to refresh the catalog, then update the
  affected parser.
* **RHP extraction returns mostly null** — the prospectus likely has a
  non-standard TOC; check the locator output in the run log and adjust
  the section anchors in `scripts/extract_rich_rhp.py`.

## Secrets

* `DEEPSEEK_API_KEY`, `INDIA_DATAHUB_API_KEY`, `KITE_*` live in `.env`
  (gitignored). `.env.example` is the template.
* No key is ever written to a data file or log. `deepseek_usage.jsonl`
  records only purpose + token counts.
* Rotate the DeepSeek key after the v2 cutover (it was a disposable key
  during the build).
