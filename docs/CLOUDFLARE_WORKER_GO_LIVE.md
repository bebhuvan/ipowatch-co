# Cloudflare Worker go-live runbook

IPOWatch V3 is deployed as a Cloudflare Worker with static assets, not
Cloudflare Pages.

## Deployable artifact

- Build command: `cd web && npm run build`
- Static asset directory: `web/dist`
- Worker config: `web/wrangler.jsonc`
- Public data source: `data/ipo_watch_v3`

The Worker uses Cloudflare Workers static assets with:

- `assets.directory = ./dist`
- `assets.not_found_handling = 404-page`
- `assets.html_handling = auto-trailing-slash`
- `_headers` copied from `web/public/_headers`

## Required GitHub secrets

- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_ACCOUNT_ID`

The API token should be scoped narrowly to deploy the `ipowatch-net` Worker and
manage custom domains for the `ipowatch.co` zone.

## Preflight gates

Before any Worker deploy, run:

```bash
.venv/bin/python scripts/audit_v3_quality.py --site-root data/ipo_watch_v3 --gate
.venv/bin/python scripts/audit_prospectus_facts.py --site-root data/ipo_watch_v3 --gate
.venv/bin/python -m ipo_portal.orchestrator audit-source-structure --gate
.venv/bin/python scripts/guard_publish_v3.py --skip-refresh-summary
cd web && npm run build && npm run cf:preview
```

`guard_publish_v3.py --skip-refresh-summary` is used for deploys from an already
committed artifact. Scheduled refresh jobs use the stricter guard that requires
`data/reports/latest_refresh_summary.json`.

## Preview deploy

Use the `Deploy IPOWatch Worker` GitHub workflow with `environment=preview`.
This deploys to the Worker preview/workers.dev surface and keeps production
routes untouched. The `_headers` file marks workers.dev URLs as `noindex`.

## Production deploy

Use the same workflow with `environment=production`.

Production custom domains in `web/wrangler.jsonc`:

- `ipowatch.co`
- `www.ipowatch.co`

Before running the production deploy:

1. Confirm the repo has a clean committed baseline.
2. Confirm the preview Worker serves the latest committed `dataset_version`.
3. Confirm the Cloudflare Worker custom domains for `ipowatch.co` and `www.ipowatch.co` are active.
4. Confirm no Cloudflare Pages project is still attached to the same hostname.

## Rollback

Rollback is a Worker version rollback or a Git revert followed by the production
deploy workflow. Do not rerun scrapers as part of rollback.

The data refresh workflows commit V3 artifacts only after gates pass. If a
refresh fails, diagnostics are uploaded and the last-good local build is
restored before the workflow can reach the commit step.
