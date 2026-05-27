# CI / go-live — v2 data pipeline

The production data is refreshed by two GitHub Actions workflows that run the
v2 pipeline and commit the data tree back to the repo. The Astro site reads
the committed `data/site_v2/` (and, during the v1→v2 frontend migration,
`data/site/` for pages not yet ported).

## Workflows

| Workflow | Schedule | Command | DeepSeek? | Purpose |
|---|---|---|---|---|
| `.github/workflows/refresh-data.yml`  | hourly (`17 * * * *`) | `refresh --skip-enrich` | no | SEBI + NSE/BSE fetch + Kite prices + v2 normalize + drift. Keeps prices & live IPO data fresh. |
| `.github/workflows/refresh-full.yml`  | daily (`30 1 * * *`, 07:00 IST) | `refresh --enrich-limit 25` | yes | Everything above **plus** go-forward RHP extraction for current IPOs. |

Both share the `site-v2-refresh` concurrency group so they never race on
`git push`. Both produce **both** `data/site` (v1) and `data/site_v2` (v2);
drop the `data/site` add from the commit step once the frontend fully reads v2.

## The commit gate

After `refresh`, each workflow runs:

```
python scripts/audit_v2_quality.py --gate
```

`--gate` exits non-zero **only** on integrity-critical findings — manifest⇄disk
count mismatch (silent record loss / stale orphans), unparseable files, bad
enums/identity. The long-tail data quirks (band edge cases, OFS date semantics,
multi-tranche NCDs) are informational and never block. A failed gate fails the
job, so the corrupt tree is never committed/pushed.

`refresh` is best-effort (each step is independently guarded); a transient NSE
network failure is logged and the cycle continues, so the gate — not refresh's
exit code — is the authoritative commit guard.

## Required GitHub repository secrets

Set under **Settings → Secrets and variables → Actions**. Missing secrets
degrade gracefully (the affected step fails, the rest of the cycle continues):

- `KITE_API_KEY`, `KITE_API_SECRET`, `KITE_USER_ID`, `KITE_PASSWORD`, `KITE_TOTP_SECRET` — Kite price fetch (TOTP auto-login).
- `DEEPSEEK_API_KEY` — RHP enrichment (daily workflow only).

Credentials are read from `os.environ`; the code falls back to `.env` only for
vars not already set, so the workflow `env:` block takes precedence. Never put
secrets in the workflow file or commit `.env`.

## Follow-ups (not launch blockers)

- **Regenerate `docs/schema/raw_catalog`** to include the new per-issue
  endpoints (`bid_details`, `issue_detail`, `consolidated_bid_details`). Drift
  currently logs ~1,450 "added" events as catalog-unknown noise; regenerating
  makes drift detection meaningful again. (`python -m ipo_portal.orchestrator catalog`.)
- **Sector classification** (`classify-sectors`, DeepSeek) is a separate manual
  job, not in the cron. New companies inherit no sector until it's run; schedule
  it weekly if auto-classification of new listings is wanted.
