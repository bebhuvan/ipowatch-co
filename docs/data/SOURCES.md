# Upstream sources

Every endpoint we scrape, with its purpose, refresh cadence, staleness
tolerance, and known quirks. New sources are added via a PR that
updates this file, `docs/data/SOURCE_PRECEDENCE.yaml`, and adds a
catalog under `docs/schema/raw_catalog/<source>/`.

Conventions:
* **Refresh cadence** — how often the live cron should re-fetch.
* **Staleness tolerance** — after how long without a successful fetch
  the dataset is considered degraded (see `EDGE_CASES.md` `E.SNP.002`).
* **Confidence tier** — `primary` (exchange of listing), `secondary`
  (other exchange / aggregator), `enrichment` (derived data).

The per-endpoint catalog files under `docs/schema/raw_catalog/` have
DeepSeek-suggested values for cadence and tolerance; this file is the
operator-curated source of truth (we override DeepSeek suggestions
where domain knowledge differs).

---

## NSE — National Stock Exchange

Base API: `https://www.nseindia.com/api/`
Warm-up: required (`HttpClient.warm_nse()` hits the landing page once
to seed cookies; subsequent JSON endpoints need `Referer` headers).

### Issue feeds

| Endpoint | URL | Cadence | Tolerance | Tier | Notes |
|---|---|---|---|---|---|
| `ipo_current_issue`         | `/api/ipo-current-issue`                       | 15min | 2h | primary | Active mainboard + SME IPOs with live subscription stats. Numbers come as strings. `E.NUM.003`. |
| `ipo_upcoming`              | `/api/all-upcoming-issues?category=ipo`        | 1h | 6h | primary | Forthcoming mainboard IPOs. |
| `ipo_public_past_issues`    | `/api/public-past-issues`                      | 24h | 3d | primary | Full historical issue list. 380KB+ payload. |
| `ipo_past_security_type`    | `/api/ipo-past-security-type`                  | 24h | 7d | primary | Past issues bucketed by security type. |
| `offer_documents_equity`    | `/api/corporates/offerdocs?index=equities`     | 6h | 24h | primary | Mainboard offer documents. 1MB+ payload. |
| `offer_documents_sme`       | `/api/corporates/offerdocs?index=sme`          | 6h | 24h | primary | SME offer documents. 1.4MB+ payload. |
| `public_issue_advertisements` | `/api/public-issue-advertisement?`           | 24h | 7d | primary | Required public advertisements. |

### OFS — Offer For Sale

| Endpoint | URL | Cadence | Tolerance | Tier | Notes |
|---|---|---|---|---|---|
| `ofs_active_general` / `_retail` / `_total_retail` / `_grouped` | `/api/live-ofs-active-issues-ss?index=...` | 15min | 2h | primary | Multiple `index` variants for the live OFS dashboard. |
| `ofs_forthcoming`           | `/api/live-ofs-forthcoming-issues`              | 1h | 6h | primary | |
| `ofs_past` / `_general` / `_retail` | `/api/live-ofs-past-issues?...`          | 24h | 7d | primary | |

### Tender, Rights, IPP, InvIT, REIT, ZCZP, LWF, NCB, GSEC, MFSS

These follow analogous patterns. See `sources.py` for the full list.
Most are daily-refresh / 7-day tolerance, `primary` tier.

### Dynamic / per-issue endpoints

Discovered from `ipo_current_issue` and `offer_documents_*`. Cadence
is **per active IPO, every 30 min** during the subscription window.

* `/api/ipo-detail?symbol=X&series=Y` — per-issue metadata.
* `/api/ipo-bid-details?symbol=X&series=Y` — bid book.
* `/api/ipo-active-category?symbol=X` — categorized subscription.
* `/api/ipo-chart-demand?symbol=X&exchange=NSE` — hourly demand chart.
* `/api/offer-documents?pan_no=P` — offer document index for a PAN.
* `/api/offer-documents-abridged-prospectus?pan_no=P&type=T` —
  prospectus sections (8 types).

Rate-limit guidance: NSE returns 429 on burst. Token-bucket at 1 req
per 500ms; back off exponentially on 429.

---

## BSE — Bombay Stock Exchange

Base API: `https://api.bseindia.com/BseIndiaAPI/api/`
Required `Referer`: `https://www.bseindia.com/publicissue`.

### Issue feeds

| Endpoint | URL | Cadence | Tolerance | Tier | Notes |
|---|---|---|---|---|---|
| `public_issue`              | `GetPublicIssue/w`                              | 1h | 6h | primary | Live public issues list. |
| `public_issue_details`      | `GetPublicIssue_par/w`                          | 1h | 6h | primary | Per-issue parameters; used to derive nested endpoints. |
| `ipo_years`                 | `IPOYear/w`                                     | 24h | 7d | primary | Available years for the IPO tracker. |
| `ipo_tracker_current_year`  | `IPOTrackerN/w?Fromdt=YYYY0101&Todt=YYYYMMDD`   | 1h | 6h | primary | Current-year tracker. |
| `ipo_documents`             | `Pubissues_IPODRHP_par_ng/w`                    | 6h | 24h | primary | DRHP / RHP / Prospectus document index. 520KB+. |

### Performance pages

| Endpoint | URL | Cadence | Tolerance | Tier | Notes |
|---|---|---|---|---|---|
| `ipo_performance_mainboard_<year>` | `MoreCompanyN/w?Fromdt=YYYY&flag=1&type=2`   | 1d | 7d | primary | One file per year, 2017–current. |
| `ipo_performance_sme_<year>`       | `MoreCompanyN/w?Fromdt=YYYY&flag=2&type=2`   | 1d | 7d | primary | One file per year, 2017–current. |

### Document feeds

`buyback_*`, `takeover_*`, `voluntary_delisting_documents`,
`rights_issue_documents`, `qip_documents`, `invit_*`, `bond_*`,
`sgb_live_issues` — daily refresh, 7-day tolerance, `primary`.

### Dynamic / per-issue endpoints (BSE)

Discovered from `public_issue_details.IPO_NO`.

* `GetMkt_ISSUE_BBS_IPO/w?IPO_NO=N` — issue detail.
* `Pubissues_GetBkbldgCatdem_ng/w?IPO_NO=N` — bid details.
* `Pubissues_GetBkbldgCatdem_PAR_bbnew_ng/w?IPO_NO=N` — consolidated
  bid details (new format).
* `Pubissues_BSEDemandSchedule_otb_ng/w?Scripcode=S&IPO_NO=N` — demand
  schedule.
* `https://www.bseindia.com/BseGraph/charts/BarChart_IPO?...` — demand
  chart HTML.

### Dropdown-dependent endpoints

* `ofs_date_list` → date dropdown driving per-date OFS detail.

Rate-limit guidance: BSE has been observed to throttle bursts. 1 req
per 500ms with explicit backoff on 429/503.

---

## Capital Market

Base: `https://www.capitalmarket.com/markets/IPOs/`
Pagination: ASP.NET POST with `__VIEWSTATE`. `E.PAG.001`.

| Endpoint | URL | Cadence | Tolerance | Tier | Notes |
|---|---|---|---|---|---|
| `ipo_historic_table_index`   | `ipo-historic-table` (page 1)  | 1d | 7d | secondary | Anchors paging signature. |
| `ipo_historic_table_page_<n>`| `ipo-historic-table` (page n)  | 1d | 7d | secondary | Replayed pages. Stop on signature change → restart from index. |
| `sme_historic_table_*`       | analogous                        | 1d | 7d | secondary | |

---

## Prime Database (demos)

Base: `https://www.primedatabase.com/pub_demo.asp?...`

Public demo pages only; full data is paywalled. Used as coverage map,
not row-level join. Refresh weekly, 30-day tolerance, `enrichment`.

---

## Trendlyne

Base: `https://trendlyne.com/ipo/api/screener-v2/year/YYYY/`

| Endpoint | Cadence | Tolerance | Tier |
|---|---|---|---|
| `upcoming`               | 6h | 24h | enrichment |
| `year_<YYYY>` (2018+)    | 1d | 7d | enrichment |

Used to fill listing-gain / current-price fields where exchange feeds
are missing. Enrichment-tier (see `EDGE_CASES.md` `E.SRC.003`).

---

## Moneycontrol

Base: `https://www.moneycontrol.com/mcapi/v1/ipo/get-listed-ipo`

Pagination: `?start=N&limit=20`. Stop on two consecutive short pages
(`E.PAG.003`). Refresh 1d, tolerance 7d, tier `enrichment`.

---

## IndiaDataHub — SEBI Capital Raising

Base: SEBI-derived monthly time series via IndiaDataHub Economic
Monitor API. Requires `INDIA_DATAHUB_API_KEY` in `.env`.

19 series covering: total / public / IPO / IPO-mainboard / IPO-SME /
FPO / rights / QIP / preferential — issue counts and ₹ raised. Refresh
monthly (server-side authority), tolerance 60 days, tier `primary` for
SEBI aggregates only.

---

## Kite Connect

Local-server only; supplies listing-day quote data. Never written to
public site data. Tier `enrichment`. Refresh: per listing event.

---

## DeepSeek

Used by the orchestrator (`ipo_portal.orchestrator`) for analytical
work — never at site-build runtime. Tier: tooling, not a data source.
All calls cached on disk under `data/cache/deepseek/`; usage logged to
`data/reports/deepseek_usage.jsonl`.
