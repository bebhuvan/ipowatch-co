# IPO Information Portal Data Pipeline

This project scrapes IPO data from the public NSE and BSE web endpoints, stores immutable raw snapshots locally, normalizes records into a static-site-friendly JSON shape, and writes validation reports for contamination and temporal leakage checks.

## Data Sources

NSE page: `https://www.nseindia.com/market-data/all-upcoming-issues-ipo`

The page currently calls these JSON endpoints:

- `https://www.nseindia.com/api/ipo-current-issue`
- `https://www.nseindia.com/api/all-upcoming-issues?category=ipo`
- `https://www.nseindia.com/api/public-past-issues`
- `https://www.nseindia.com/api/ipo-past-security-type`
- `https://www.nseindia.com/api/live-ofs-active-issues-ss?index=RS`
- `https://www.nseindia.com/api/live-ofs-active-issues-ss?index=IS`
- `https://www.nseindia.com/api/live-ofs-active-issues-ss?index=totalForRetail`
- `https://www.nseindia.com/api/live-ofs-forthcoming-issues`
- `https://www.nseindia.com/api/live-ofs-past-issues`
- `https://www.nseindia.com/api/live-ofs-past-issues?index=GENERAL`
- `https://www.nseindia.com/api/live-ofs-past-issues?index=RETAIL`
- `https://www.nseindia.com/api/liveTenderActive-issues`
- `https://www.nseindia.com/api/liveTenderForthcoming-issues`
- `https://www.nseindia.com/api/liveTenderPast-issues`
- `https://www.nseindia.com/api/liveWatchRights-issues?index=activeIssues`
- `https://www.nseindia.com/api/liveWatchRights-issues?index=pastIssues`
- `https://www.nseindia.com/api/liveIppActive-issues`
- `https://www.nseindia.com/api/liveIppPast-issues`
- `https://www.nseindia.com/api/invits-current-issues`
- `https://www.nseindia.com/api/invits-past-issues`
- `https://www.nseindia.com/api/reits-current-issues`
- `https://www.nseindia.com/api/reits-past-issues`
- `https://www.nseindia.com/api/zczp-active-issue`
- `https://www.nseindia.com/api/zczp-forthcoming`
- `https://www.nseindia.com/api/zczp-past-issue`
- `https://www.nseindia.com/api/corporates/offerdocs?index=equities`
- `https://www.nseindia.com/api/corporates/offerdocs?index=sme`
- `https://www.nseindia.com/api/corporates/offerdocs/equity/companylist`
- `https://www.nseindia.com/api/corporates/offerdocs/sme/companylist`
- `https://www.nseindia.com/api/public-issue-advertisement?`
- `https://www.nseindia.com/api/ipo-issue-company-list`

BSE page: `https://www.bseindia.com/publicissue`

The current public issue and IPO document feeds used here are:

- `https://api.bseindia.com/BseIndiaAPI/api/GetPublicIssue/w`
- `https://api.bseindia.com/BseIndiaAPI/api/GetPublicIssue_par/w`
- `https://api.bseindia.com/BseIndiaAPI/api/IPOYear/w`
- `https://api.bseindia.com/BseIndiaAPI/api/IPOTrackerN/w?Fromdt=YYYY0101&Todt=YYYY1231`
- `https://api.bseindia.com/BseIndiaAPI/api/Pubissues_IPODRHP_par_ng/w`
- BSE raw archival feeds for OFS dates, buyback documents, takeover documents, voluntary delisting, rights/QIP, InvIT/REIT, bond, and SGB issue documents.
- `https://api.bseindia.com/BseIndiaAPI/api/MoreCompanyN/w?Fromdt=YYYY&company=&flag=1&type=2`
- `https://api.bseindia.com/BseIndiaAPI/api/MoreCompanyN/w?Fromdt=YYYY&company=&flag=2&type=2`

CapitalMarket historical performance pages:

- `https://www.capitalmarket.com/markets/IPOs/ipo-historic-table`
- `https://www.capitalmarket.com/markets/IPOs/sme-historic-table`

These pages are stored as raw HTML snapshots and replay ASP.NET postback pagination. Each table also writes an index snapshot that lists the current valid page endpoints, so stale page snapshots are not normalized if the site later changes its page count.

PRIME Database public demo pages:

- `https://primedatabase.com/pub_demo.asp`
- Adjacent public demo pages for SME issues, rights, public debt, QIP, IPP, InvIT/REIT, SSE, IDR, takeover, delisting, buyback, preferential equity, preference shares, debt private placements, commercial paper, certificates of deposit, overseas offerings, and block deals.

These pages expose service/coverage metadata rather than issue-level rows. They are stored as raw HTML snapshots and written to `data/site/prime/*` for source intelligence, coverage summaries, and roadmap decisions.

Trendlyne IPO APIs:

- `https://trendlyne.com/ipo/api/upcoming/`
- `https://trendlyne.com/ipo/api/screener-v2/year/YYYY/` for `2018` through the current `--as-of` year.

Trendlyne is used as a third-party IPO enrichment source for 2018+ IPO rows, including DRHP/RHP links, bid dates, issue size, price band, issue price, SME flag, exchange flags/codes, subscription, listing gains, and current gains. Raw JSON is retained; normalized values should be treated as enrichment, not as the official source of record.

Moneycontrol listed IPO API:

- `https://api.moneycontrol.com/mcapi/v1/ipo/get-listed-ipo?start=N&limit=20`

The listed IPO page at `https://www.moneycontrol.com/ipo/listed-ipos/` is paginated by the React app with 20-row `start` offsets. The fetcher follows that API until the terminal short page, stores each page as raw JSON, and writes a `listed_ipos_index` snapshot listing the active page endpoints for that run. This source provides listing date, IPO type, issue price/size, subscription, listing open/close performance, latest traded price, and current gain. Treat it as third-party performance enrichment, not as the official exchange source of record.

Nested current-issue resources are also fetched where issue identifiers are available:

- NSE issue detail: `/api/ipo-detail?symbol=SYMBOL&series=SERIES`
- NSE bid details: `/api/ipo-bid-details?symbol=SYMBOL&series=SERIES`
- NSE consolidated bid details: `/api/ipo-active-category?symbol=SYMBOL`
- NSE demand data: `/api/ipo-chart-demand?symbol=SYMBOL&exchange=NSE`
- NSE all-exchange demand data: `/api/ipo-chart-demand?symbol=SYMBOL&exchange=ALL`
- NSE offer document details for current/public-ad issues: `/api/offer-documents?pan_no=PAN`
- NSE abridged prospectus detail sections for current/public-ad issues: `/api/offer-documents-abridged-prospectus?pan_no=PAN&type=TYPE`
- BSE issue detail: `/GetMkt_ISSUE_BBS_IPO/w?IPO_NO=IPO_NO`
- BSE bid details: `/Pubissues_GetBkbldgCatdem_ng/w?IPO_NO=IPO_NO`
- BSE consolidated bid details: `/Pubissues_GetBkbldgCatdem_PAR_ng/w?IPO_NO=IPO_NO`
- BSE consolidated bid details, newer format: `/Pubissues_GetBkbldgCatdem_PAR_bbnew_ng/w?IPO_NO=IPO_NO`
- BSE demand schedule: `/Pubissues_BSEDemandSchedule_otb_ng/w?Scripcode=SCRIP&IPO_NO=IPO_NO`
- BSE demand graphs: `/BseGraph/charts/BarChart_IPO?Scripcode=SCRIP&ir_flag=IPO&CType=B|C`

## Quick Start

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m ipo_portal fetch
```

Outputs:

- `data/raw/<source>/<endpoint>/<timestamp>.json`: raw response snapshots with metadata and SHA-256 hash.
- `data/processed/ipos.json`: normalized records from all sources.
- `data/site/ipos.json`: legacy deduplicated issue list; records with hard errors are excluded.
- `data/site/manifest.json`: entry point for Astro/Hugo builds.
- `data/site/issues/index.json`: all publishable issue records in the structured site schema.
- `data/site/issues/current.json`: current/live issues.
- `data/site/issues/upcoming.json`: upcoming issues.
- `data/site/issues/historical.json`: dated closed issues.
- `data/site/issues/documents.json`: document-stage records without reliable live issue dates.
- `data/site/issues/ofs.json`: NSE offer-for-sale records.
- `data/site/issues/tender.json`: NSE tender-page records, including buybacks.
- `data/site/issues/buybacks.json`: buyback records from the NSE tender feeds.
- `data/site/issues/rights.json`: NSE rights issue records.
- `data/site/issues/ipp.json`: NSE IPP records.
- `data/site/issues/invits.json`: NSE InvIT public issue records.
- `data/site/issues/reits.json`: NSE REIT public issue records.
- `data/site/issues/zczp.json`: NSE ZCZP issue records.
- `data/site/issues/performance.json`: issues with listing/current performance fields, including BSE, CapitalMarket, Trendlyne, and Moneycontrol historical IPO rows.
- `data/site/prime/demo_pages.json`: parsed PRIME demo-page service modules and coverage metadata.
- `data/site/prime/coverage.json`: flattened PRIME annual coverage rows by product.
- `data/site/prime/summary.json`: grouped PRIME source-intelligence summary.
- `data/site/issues/by-slug/*.json`: one route-ready file per issue.
- `data/site/companies/index.json`: all companies with issue references.
- `data/site/companies/by-slug/*.json`: one route-ready file per company.
- `data/site/indexes.json`: grouped indexes by status, year, exchange, issue type, and company.
- `data/site/schema.json`: schema contract for site consumers.
- `data/reports/validation.json`: hard errors and warnings.
- `data/reports/quarantine.json`: records excluded from `data/site/ipos.json`.

## Astro Data Contract

Use `data/site/manifest.json` as the stable entry point. It contains schema version, build date, record counts, and relative paths to the main collections.

Each issue in `data/site/issues/index.json` has this shape:

- `id`, `slug`, `url_path`, `title`
- `company`: stable company id, name, slug, URL path, symbol
- `classification`: status, issue type, security type, exchange/platform
- `timeline`: open, close, and listing dates
- `pricing`: price band, issue price, face value
- `issue_size`: text and shares offered
- `subscription`: bid shares and subscription multiple
- `listing_performance`: listing open/close, listing open gain, current price, current gain/loss, Moneycontrol current gain from listing open where available, and stock URL
- `exchange_details`: per-exchange nested data, including issue detail, bid details, consolidated bid details, demand data/schedules, and demand graph URLs
- `exchange_details.nse.ofs_details`: OFS category rows from NSE.
- `exchange_details.nse.tender_details`, `tender_bid_details`, and `tender_demand`: tender/buyback row data and live demand/bid details from NSE.
- `documents`: DRHP/RHP/prospectus, XBRL, audiovisual, and public-advertisement links where available
- `data_quality`: `clean`, `review`, or `blocked`, plus attached validation findings
- `redactions`: fields removed because they would leak future information for `--as-of`
- `sources`: exchange provenance for audit and debugging

Recommended Astro usage:

```ts
import manifest from '../data/site/manifest.json';
import currentIssues from '../data/site/issues/current.json';
import performanceIssues from '../data/site/issues/performance.json';
import primeCoverage from '../data/site/prime/coverage.json';
import allIssues from '../data/site/issues/index.json';
```

## Useful Commands

Fetch all source data:

```bash
python -m ipo_portal fetch
```

Fetch with an explicit as-of date for validation:

```bash
python -m ipo_portal fetch --as-of 2026-05-22
```

Validate already-normalized data:

```bash
python -m ipo_portal validate --as-of 2026-05-22
```

`fetch`, `build-site`, and `validate` return a non-zero exit code when hard validation errors are found. This is intentional so CI can block contaminated data from publishing.

Build only the static-site JSON from existing raw snapshots:

```bash
python -m ipo_portal build-site --as-of 2026-05-22
```

For scheduled snapshot capture where quarantined rows should not fail the run:

```bash
python -m ipo_portal fetch --as-of "$(date -u +%F)" --allow-validation-errors
```

The repository includes `.github/workflows/fetch-ipo-data.yml`, which runs hourly and uploads `data/raw`, `data/processed`, `data/site`, and `data/reports` as a workflow artifact. Keep `data/raw` out of git; publish `data/site` from your Astro build or sync the generated bundle to object storage.

## Validation Model

The validator is designed for exchange data that may contain stale fields, malformed dates, or values that should not appear in a historical point-in-time build.

Hard errors:

- Missing company name.
- End date before start date.
- Listing date before issue close date.
- Duplicate source record IDs in the same normalized file.
- A source record ID reused for multiple companies or date windows.
- Snapshot observation time later than the requested `--as-of` date.
- Static-site merge-key collisions where different company names would be merged.

Warnings:

- Source status disagrees with date-derived status.
- Future records leaking into a point-in-time build.
- Listing date visible before issue close date in a point-in-time build.
- Issue price visible before issue close date.
- Future listing date visible in a point-in-time build.
- BSE/NSE date mismatches for likely-matching companies.
- Suspicious numeric/date strings that could not be parsed.

Safeguards:

- Every raw response is saved with endpoint metadata and a SHA-256 hash before normalization.
- The normalized feed keeps `source`, `source_endpoint`, `source_record_id`, and `observed_at` for provenance.
- Point-in-time builds fail if they are made from snapshots observed after the chosen `--as-of` date.
- Records with hard validation errors are quarantined and not published to the site JSON.
- Future-sensitive fields such as `listing_date` and `issue_price` are redacted from `data/site/ipos.json` when they are not safe for the requested `--as-of` date.

For a public site, use `data/site/ipos.json`; for auditability, keep `data/raw` under backup or object storage even if you do not commit it.
