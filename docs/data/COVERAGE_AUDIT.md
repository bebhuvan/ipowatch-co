# Coverage audit — what the v2 normalizer is missing (2026-05-23)

Triggered by the Astro builder's observation that v2 records are
*cleaner but narrower* than the old site: sectors/dealmakers absent,
subscription splits and live demand thin. This audit rechecks every
NSE/BSE page against what the v2 normalizer actually extracts.

## Verdict

We built breadth (every issue type, every *list* feed) but skipped the
**per-issue detail + bid-book endpoints**, which is exactly where
book-building, subscription category splits, lot size, BRLM, registrar,
anchor, and the demand curve live. We *fetch* most of it into
`data/raw/` (the nested `issue_detail_*` / `*bid_details_*` /
`demand_*` endpoints) — the normalizer just never parses it.

Two of these (subscription splits + demand) are *already extracted* by
`trajectory_v2` for the time-series, but the **final summary is never
written onto the issue record**.

## What's fetched but unparsed

| Raw endpoint | Carries | Parsed into issue record? |
|---|---|---|
| `bse/issue_detail_<id>` | BRLM, co-BRLM, registrar, sponsor bank, syndicate, **market lot, min bid qty, tick size**, face value, price band, issue size, **anchor details**, **daily subscription DY1–DY6**, max bid QIB/NII, **demand schedule (price→qty)**, rating, notices/corrigendum/addendum | ❌ no |
| `nse/issue_detail_<id>` | subscription category splits, bid details, **demand graph plot data**, overall times, total issue size | ❌ no |
| `bse/consolidated_bid_details_new_<id>` | full category book: QIB(+FII/DFI/MF), NII(+>10L/≤10L), Retail, Employee, Shareholder, Policyholder — offered/bid/times | ⚠️ time-series only, no final summary on record |
| `nse/consolidated_bid_details_<id>` / `nse/bid_details_<id>` | NSE category splits | ⚠️ same |
| `bse/demand_schedule_<id>` | demand at each price point (book-building curve) | ❌ no |
| `bse/demand_graph_*`, `nse/demand_data_*` | demand chart series | ❌ no |

## The specific missing metrics

**Book building / subscription** (the big one):
- Per-category subscription: QIB / FII / DFI / MF / NII(>10L) / NII(≤10L)
  / Retail / Employee / Shareholder / Policyholder — shares offered,
  shares bid, times subscribed.
- Anchor allocation + anchor investors.
- Day-by-day subscription progression (DY1–DY6).
- Demand schedule (price → cumulative quantity) — the actual book.
- Max bid quantity per category.

**Issue mechanics:**
- **Lot size / market lot**, minimum bid quantity, tick size.
- Authoritative price band + face value for the active issue.

**Parties (dealmakers):**
- Book Running Lead Manager(s) + co-BRLM (pipe-separated in BSE).
- Registrar, sponsor bank, syndicate members, eligible/SCSB banks.

**Classification:**
- Sector / industry — not in any structured feed; comes from the RHP
  prospectus extract (`industry_landscape.industry_name`,
  `company_about.sector`) or a sector map.

## Why coverage is thin *historically*

The per-issue endpoints are only fetched for **active** IPOs (the
nested-endpoint discovery in `sources.py` fires when an issue is
current). So we have raw `issue_detail` / bid data for only ~6–12 issues
— whatever was live during our fetch windows. Going forward, the hourly
cron captures full book-building for every active IPO.

For history, the BSE `issue_detail` and bid endpoints accept an
`IPO_NO` and BSE serves past issues — so a one-time **backfill by
IPO_NO** (cheap API calls, no DeepSeek) can recover lot size / BRLM /
subscription splits for past mainboard + SME IPOs. (NSE is symbol-keyed
and tends to drop closed issues, so NSE history is patchier.)

## Status (2026-05-23)

**Built & verified:**
- Schema: added `parties`, `book_building`, `subscription.consolidated`,
  `subscription.by_exchange` (root stays strict).
- `bse_issue_detail` parser → market lot, tick size, face value, price
  band, BRLM/co-BRLM, registrar, sponsor bank, syndicate, demand
  schedule. Merges via the `bse:ipo_no` alias (new alias-union in
  consolidation).
- `bse_bid_summary` parser → keeps **both** the consolidated (NSE+BSE)
  book and the per-exchange BSE book (`subscription.consolidated` +
  `subscription.by_exchange.bse`), reusing the v1 category mapper.
- `backfill_detail.py` → sweeps historical `IPO_NO`s to recover the
  above for past issues. Verified end-to-end on the 6 currently-active
  issues (e.g. Harikanta Overseas: 6 sources merged, lot 1200, registrar,
  both books).

**Remaining:**
- Run the full historical backfill:
  `python -m ipo_portal.backfill_detail --start 1 --end 7800`
  (~1h one-time; cheap API calls, resumable) then `normalize`. Until
  then, rich per-issue data exists only for issues fetched while active.
- `nse_issue_detail` parser (NSE-side subscription splits + demand graph).
- Sector / industry classification (derive from prospectus extract;
  DeepSeek bulk-classification for the rest).

## Source-page completeness by issuance type (2026-05-23 re-audit)

Probed the live NSE/BSE APIs against every primary-issuance type:

| Type | NSE | BSE | Status |
|---|---|---|---|
| Equity IPO/FPO | current/upcoming/past + offerdocs + per-issue detail | GetPublicIssue(+_par) + performance + per-issue detail | ✅ full |
| OFS | ofs_active/forthcoming/past (+retail/general) | inline `OFS` flag in GetPublicIssue | ✅ (BSE per-date dropdown results not fetched — NSE covers) |
| Buyback | tender_active/forthcoming/past | `OTB` flag (now mapped) + buyback tender/open-mkt docs | ✅ |
| Debt / NCD | public-past-issues securityType N0/DEBT → NCD | `DPI` flag (now mapped) + bond_issue_documents | ✅ |
| Rights | rights_active/forthcoming/past | `RI` flag + rights_issue_documents | ✅ |
| InvIT / REIT | invits/reits current+past | invit placement + INVITS/REITS docs | ✅ |
| SGB | (NSE endpoint is secondary-market only) | sgb_live_issues | ✅ (BSE) |
| QIP / IPP / ZCZP / takeover / delisting | IPP, ZCZP (NSE) | QIP, takeover, delisting docs | ✅ |

**Bug found & fixed:** BSE `GetPublicIssue` carries `DPI` (debt public
issue) and `OTB` (buyback) inline, but the parser mapped only
IPO/FPO/OFS/RI/BB → these fell to "Others". Now `DPI→NCD`, `OTB→Buyback`,
`CMN→Others`.

**Examined and intentionally NOT added:**
- NSE `offerdocs?index=debt` (30,840 rows) — the whole debt-segment
  firehose (commercial paper, private placements, disclosures), not
  clean public NCDs. BSE `DPI` + NSE securityType=N0 are the clean debt
  sources.
- NSE `sovereign-gold-bonds` — secondary-market quote board, not primary.
- BSE OFS per-date results behind `ofs_date_list` — NSE OFS feeds cover
  this; revisit if BSE-only OFS history is needed.

**Verdict:** with the DPI/OTB fix, all primary issuance types are
covered across both exchanges. Remaining depth work is the historical
per-issue backfill (below) and the NSE-side per-issue detail parser.

## Fix plan

1. **Schema** — add `parties` (lead managers, registrar, sponsor bank,
   syndicate), `book_building` (daily subscription, demand schedule),
   and flesh out `pricing` (lot_size_shares, tick_size_paise,
   min_bid_qty) + `subscription.categories` final summary.
2. **Parsers** — `bse_issue_detail`, `nse_issue_detail`,
   `bse_bid_summary` (final category subscription onto the record). Reuse
   v1 `trajectory.py` category mappers.
3. **Backfill scraper** — fetch `issue_detail` + `consolidated_bid_details`
   for historical `IPO_NO`s (from `bse/public_issue_details`), so the
   metrics apply to past issues, not just future ones.
4. **Sector** — derive from prospectus extract where present; otherwise a
   sector-mapping pass (DeepSeek is appropriate here — bulk classification).
