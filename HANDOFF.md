# IPO Watch — handoff for the next Claude Code session

> **Single instruction for the next session:** read this whole file before touching anything. The user has iterated heavily on design and rejected several directions; the choices already made are the foundation, not negotiable defaults to second-guess. Where this doc disagrees with what you see in the code, the **code is current**.

---

## 1. What we're building

`IPO Watch` is an editorial database of Indian public issues at `ipowatch.co` — every IPO, FPO, OFS, rights issue, buyback, tender, REIT, InvIT and IPP on the National (NSE) and Bombay (BSE) stock exchanges. The product goal is to beat Chittorgarh / Moneycontrol / Tickertape on **clarity, refinement, and data depth** — without their clichés (no GMP, no broker affiliate links, no "Apply Now" buttons, no fintech-dashboard tropes).

Status: design system locked, home + live + upcoming + archive + per-issue pages built, IndiaDataHub SEBI data integrated for capital-raising aggregates. Substantial backlog of new sections and richer historicals still to build.

---

## 2. Repo layout

```
IPO/
├── ipo_portal/              Python scrapers + builders. Run via `python -m ipo_portal …`
│   ├── cli.py               Subcommands: fetch, build-site, validate
│   ├── datahub.py           NEW — IndiaDataHub SEBI capital-raising fetcher
│   ├── capitalmarket.py     Capital Market history scraper
│   ├── prime.py             PRIME database scraper
│   ├── trendlyne.py         Trendlyne IPO scraper
│   ├── moneycontrol.py      Moneycontrol scraper
│   ├── sources.py, http.py  NSE/BSE source endpoints + HTTP client
│   ├── normalize.py         Merges sources → site JSON
│   ├── site_builder.py      Produces data/site/* outputs
│   ├── validate.py          Schema validation
│   └── storage.py           File I/O, snapshots
├── data/
│   ├── raw/                 Per-source raw snapshots (gitignored)
│   ├── processed/           Normalized intermediates (gitignored)
│   └── site/                What Astro reads (gitignored, but produced reproducibly)
│       ├── manifest.json          Counts, paths, build timestamp
│       ├── indexes.json           Companies index
│       ├── ipos.json              All issues, flat
│       ├── schema.json            Quality states, allowed values
│       ├── issues/
│       │   ├── index.json         All issues
│       │   ├── current.json       10 issues open right now
│       │   ├── upcoming.json      Calendar-confirmed but not yet open
│       │   ├── historical.json    7,848 past issues
│       │   ├── buybacks.json, ofs.json, rights.json, tender.json, invits.json, reits.json, ipp.json, zczp.json
│       │   ├── performance.json   6,769 issues with day-one + return-since data
│       │   ├── documents.json     3,796 issues with offer documents
│       │   ├── by-year/[YYYY].json
│       │   └── by-slug/[slug].json   The detail-page payload, per issue
│       ├── companies/
│       ├── prime/                  PRIME database coverage rows
│       └── datahub/
│           └── capital_raising.json   NEW — SEBI monthly series + annual rollups
├── web/                     Astro static site
│   ├── astro.config.mjs     @data alias → ../data/site/
│   ├── src/
│   │   ├── styles/global.css     Design tokens, .headline/.section-title/.figure/etc.
│   │   ├── layouts/Base.astro    Masthead, footer, font loading
│   │   ├── lib/
│   │   │   ├── issues.ts         Data access + computed helpers
│   │   │   └── format.ts         ₹ / date / times formatters
│   │   ├── components/
│   │   │   ├── TrajectoryChart.astro   Subscription trajectory line chart
│   │   │   └── Sparkline.astro
│   │   └── pages/
│   │       ├── index.astro                 home — lead, banner, open cards, pipeline, year-in-numbers, recently listed, other-types, all-time best, archive
│   │       ├── live/index.astro            Open card grid
│   │       ├── upcoming/index.astro        Upcoming card grid
│   │       ├── archive/index.astro         Year overview + per-year list
│   │       ├── archive/[year].astro        Year detail
│   │       └── ipos/[slug].astro           Per-issue detail page
│   └── public/mocks/        Static HTML design mocks (1.html–6.html, plus index.html). Mock 6 is the locked design.
├── .env                     INDIA_DATAHUB_API_KEY=…  (gitignored; never commit, never echo)
├── .env.example             Template (committed)
└── .gitignore               .env*, secrets/, *.key, *.pem all ignored
```

---

## 3. How data flows

```
NSE / BSE / Capital Market / Trendlyne / Moneycontrol      ─┐
                                                            │
              Python scrapers (ipo_portal/)                 │
                                                            │
  raw snapshots ─→ normalize ─→ validate ─→ data/site/      │
                                                            │
IndiaDataHub Economic Monitor (SEBI series)  ──→ datahub.py ┤
                                                            │
                                                            ▼
                                          Astro builds static HTML from data/site/
```

The user's pipeline lives in `ipo_portal/`. The Astro app **does not call APIs at runtime** — it reads JSON from `data/site/` at build time. To refresh data, the user runs the Python pipeline; to rebuild the site, they run `npm run build` in `web/`.

---

## 4. Design system (locked — do not propose alternatives)

This was hard-won across many iterations. The user explicitly rejected three directions before landing here. Treat the current design as the foundation.

**Typography**
- Display + body serif: **Newsreader** (Production Type, free on Google Fonts, variable). Chosen specifically because Cormorant Garamond was deemed "too literary for finance." Don't switch back without explicit user approval.
- UI / labels / tabular: **Inter**.
- **Italic is rare.** Reserved for: the wordmark, the banner pull-quote, inline `<em>` emphasis, small Roman-numeral ornaments on cards, italic placeholder asides ("Markets closed"), and one archive flourish. Everywhere else is Roman. Past iterations had italic everywhere; the user pushed back hard.

**Colour palette** (defined in `web/src/styles/global.css`)
- `--color-paper`: `#ffffff` — pure white background
- `--color-ink`: `#0f0e0c`
- `--color-ink-soft`: `#2a2825`
- `--color-mute`: `#6f6d66`
- `--color-mute-2`: `#b3b1a8`
- `--color-rule`: `#ebe7da`, `--color-rule-card`: `#ebe7d9`
- `--color-accent`: `#e8765a` (soft coral — rationed: live pulse, urgent tags, card hover, italic emphasis, certain numerals)
- `--color-accent-deep`: `#d05a3f` (hover-only deeper coral)
- `--color-gold`: `#b07a35` (rarer than coral; used for peak-year highlights, "Opens" pipeline verb)

**Layout**
- `.page` 1320px max, `.page-tight` 1200px max
- White cards with `1px solid var(--color-rule-card)` border (no shadow, no rounded corners)
- Card hover: border picks up coral, subtle `#fffaf7` background tint, no lift
- Roman-numeral ornament top-right of each open-issue card (small italic muted, comes alive in coral on hover)
- Section padding 96px vertical

**Rejected** (do not bring back without asking):
- Warm paper page backgrounds
- Cormorant Garamond
- Newspaper-front aesthetic (`Cut-off`, vol/issue numbering, kicker tracked-out caps, italic display everywhere)
- "Featured cards on grey tile" fintech-dashboard look
- Card grids with rounded corners + shadows

---

## 5. Pages built — current state

| Path | What it shows | Notes |
|------|--------------|-------|
| `/` | Lead + banner + open cards (6) + week-ahead vertical timeline + "Year in capital markets" (SEBI numerals, by-instrument grid, stacked annual chart, IPOs/year chart, day-one distribution) + recently listed + other public issues (buybacks/OFS/rights) + all-time best listing days + archive year chart | Pipeline computed for next 7 days. Lead picked by largest raise. Dedupes NSE/BSE same-company duplicates. |
| `/live/` | All open issues as cards | Subscription progress bar inline |
| `/upcoming/` | Upcoming issues as cards | Grouped chronologically |
| `/archive/` | Year overview + per-year list | Year mini-chart with peak/current highlighted |
| `/archive/[year]/` | Year detail page | 4-stat band + top-5 best + full year list (200 cap) |
| `/ipos/[slug]/` | Per-issue detail | Hero figure, specs cards, subscription split (when data), trajectory chart, bid book, parties, documents, provenance |

The static design mocks at `/web/public/mocks/` are kept for reference: `6.html` is the canonical locked design. Don't delete them.

---

## 6. Data we have but **don't yet surface well**

This is the brief for the next session. The pipeline already captures a lot more than the pages show. Audit `data/site/issues/by-slug/*.json` for the per-issue richness.

**Currently in detail JSON, not yet surfaced everywhere**:
- `pricing.face_value` — surfaced on detail only
- `pricing.issue_price` — surfaced for past issues
- `issue_size.shares_offered` and `issue_size.text` — only on detail
- `listing_performance.listing_day_close`, `current_price`, `gain_loss`, `stock_url` — surfaced on detail; not in any listing/archive/comparison view
- `subscription.trajectory[]` — full hour-by-hour timestamped category-split data, only 3 issues have it but rendered nicely; should be the marquee data view for those issues
- `subscription.times` — the final total at close, often missing for old issues
- `exchange_details.bse.issue_detail.data.IPONO_0[0]` — BRLM, registrar, sponsor bank, market lot, face value, issue-size shares (rich BSE-side metadata)
- `exchange_details.bse.issue_detail.data.IPONO_2[]` — **price-level bid book** (the most distinctive datapoint on the site) — rendered on detail only
- `exchange_details.nse.issue_detail.data.issueInfo.dataList` — NSE key-value list of lot size, price range, face value, etc.
- `exchange_details.nse.abridged_prospectus.*` — when populated, BRLM list, promoter info, objects of the issue (mostly empty in current SME records but real for mainboard)
- `documents[]` — DRHP / RHP / Price Band Ad / Pre-IPO ads — rendered on detail; could power a "documents timeline" view per issue
- `sources[]` — provenance trail with `observed_at` timestamps
- `data_quality` — state + warning_count

**Data we *could* aggregate but haven't**:
- **Lead-manager (BRLM) league table** — extracted per-issue in `extractIngredient()`. Could power a `/bankers/` page ranking lead managers by deal count, ₹ raised, average listing-day gain.
- **Registrar league table** — same story.
- **Sector / industry breakdown** — would need new scraping from CapMkt or PRIME pages. Not currently captured.
- **Issue-size leaderboards** — biggest IPOs by ₹ raised, all-time. Possible if we backfill `issue_size.text` parsing across historical.
- **Most-subscribed all-time** — possible for the few issues with `subscription.times`.
- **Per-company timeline** — many companies have multiple issues (rights + IPO + buyback). `/companies/[slug]/` could render a vertical timeline.
- **Listing-gain percentile per year** — possible from `performance.json`.

**SEBI numbers wired via IndiaDataHub** (already in `data/site/datahub/capital_raising.json`):
- 18 monthly series 2012-2026 covering: issues total count, public issues count, IPO/IPO-mainboard/IPO-SME/FPO/Rights/QIP/Preferential counts; ₹ Cr raised for each of those.
- Annual rollups computed in the fetcher.
- Surfaced in the home's "{Year} in capital markets" section. Could also feed: per-year archive page, a `/markets/` data dashboard, monthly trend per type.

**Data counts** (from `manifest.json`):
- 11,662 total issues; 8,285 companies
- Issue types: 8,540 IPO + 397 OFS + 572 Rights + 208 Tender + 193 Buyback + 17 InvIT + 17 IPP + 9 REIT + 7 ZCZP
- 6,769 issues have listing-day performance data
- 3,170 issues have at least one document
- Only **3 issues** have hourly subscription trajectory captures (Vegorama Punjabi Angithi, Harikanta Overseas, etc. — the few currently live during the build window)
- Only **8 issues** have full bid-book detail
- **463 PRIME coverage rows** — pulled but not yet rendered

---

## 7. Known issues to address

1. **YoY math reads alarming early in year.** "{Year} in capital markets" shows YTD vs same-months-last-year. In Jan-Mar 2026 (3 months of data) the figure is `-65%` vs Jan-Mar 2025 because last year's same months were lighter. Labels should make the apples-to-apples nature clearer (e.g. "Jan-Mar '26 vs Jan-Mar '25" rather than `'25 YTD`).
2. **Per-type pages don't exist yet.** Home mentions buybacks / OFS / rights and lists 5 recent of each, but `/buybacks/`, `/ofs/`, `/rights/` don't exist. Tap targets lead nowhere.
3. **No `/companies/[slug]/` page.** Companies referenced in `indexes.json` but no per-company route.
4. **No `/bankers/` or `/registrars/` page.** Data is in detail JSONs but no aggregation view.
5. **No search.** Static site → search needs either a client-side search index (Pagefind / minisearch) or a separate API. Not started.
6. **Some 2026 SME duplicates** (NSE-side upcoming entries that mirror BSE-side current entries). Deduped on home by normalized company name, but not on `/upcoming/` page.
7. **`getYearStats(year)` uses Trendlyne data scope** (all issue types) while SEBI data uses official-IPO scope. The two counts differ. The home currently mixes them — top-stat from SEBI, recently-listed from Trendlyne. Worth clarifying.
8. **Detail page hero figure logic** picks the wrong figure for some issue types (e.g. buybacks have a flat "at" price, not a band; the hero treats it as a price band).
9. **Subscription split chart** only renders for issues with `subscription.trajectory[]`. Issues with only `subscription.times` (no per-category breakdown) get a hero figure but no chart. Fine for now; flag if the user notices.
10. **`/about/` route was deleted.** No about page. Adding one is a design call.

---

## 8. The next session's brief

The user explicitly wants you to do **all** of:

1. **Audit `data/site/issues/by-slug/*.json`** — pick a dozen across exchanges, types, eras (a mainboard 2024 IPO; a recent SME; an OFS; a rights issue; an old 2008 listing; a buyback). Catalogue every datapoint that exists in any of them.

2. **Propose 5-7 concrete new sections / pages / charts** that would make the site materially more data-rich and more insightful. The user specifically called out:
   - Historical IPO display with issue price, listing price, listing-day gain, current price, return-since-listing.
   - Lead-manager / registrar leaderboards.
   - Per-company timeline pages.
   - Sector / industry breakdowns (if data exists; if not, propose a scrape).
   - Subscription dynamics for the rare issues with trajectory data.

3. **Don't build first.** Propose with short ASCII sketches or layout descriptions, get the user to pick, then build. The user has rejected many directions; saving build time by proposing first is the right move.

4. **Don't touch the design language.** Use the existing tokens (`--color-accent`, `.headline`, `.section-title`, `.figure`, etc.) and the existing card pattern. If you need a new component, match the existing aesthetic exactly.

5. **The user's preference rhythm**: rejects ornament-for-its-own-sake; rejects fintech clichés; loves dense data shown calmly; loves real numbers in the section copy ("Three hundred and eighty-three companies have made it to the exchanges this year"). Editorial voice in moderation, not as performance.

---

## 9. Security note

- API key is in `/.env` as `INDIA_DATAHUB_API_KEY=…`. **Never echo it. Never commit it.** `.env` is gitignored along with `.env.*`, `secrets/`, `*.key`, `*.pem`. `.env.example` is the template.
- Project is not yet a git repo. When it becomes one, the `.gitignore` will catch the key.
- The key is used **server-side only** during the Python data-fetch phase. The static Astro build doesn't embed it.
- Refresh SEBI data with: `cd "/home/bhuvanesh.r/Documents/Bhuvan projects/IPO" && .venv/bin/python -m ipo_portal.datahub`

---

## 10. Useful commands

```bash
# Dev server (Astro)
cd "/home/bhuvanesh.r/Documents/Bhuvan projects/IPO/web" && npm run dev
# Hits whatever port Astro lands on — currently observed at http://127.0.0.1:4324/

# Refresh SEBI capital-raising data
cd "/home/bhuvanesh.r/Documents/Bhuvan projects/IPO" && .venv/bin/python -m ipo_portal.datahub

# Refresh full pipeline (NSE/BSE/CapMkt/Trendlyne/Moneycontrol/PRIME)
.venv/bin/python -m ipo_portal fetch --source all
.venv/bin/python -m ipo_portal build-site --source all
.venv/bin/python -m ipo_portal validate

# Inspect a single issue's full data shape
python3 -c "import json; print(json.dumps(json.load(open('data/site/issues/by-slug/<slug>.json')), indent=2))"

# Quick verify all main routes
for p in / /live/ /upcoming/ /archive/ /archive/2024/ /ipos/<some-slug>/; do
  curl -s -o /dev/null -w "$p %{http_code}\n" "http://127.0.0.1:4324$p"
done
```

---

## 11. Memory the new session should pre-load

The user's permanent memory at `~/.claude/projects/-home-bhuvanesh-r-Documents-Bhuvan-projects-IPO/memory/MEMORY.md` is loaded automatically by the harness. It has design preferences, scale architecture commitments, and prior-iteration scars. Read it. Some of its entries pre-date the current locked design (e.g. it still mentions "newspaper aesthetic" as locked — that was superseded). Trust the **code state** when memory and code disagree; flag the contradiction to the user before updating either.

---

## 12. Don't repeat these mistakes

- Iterating on font choice in 4-step ping-pong cycles instead of asking the user to point at a real reference site.
- Building a 2,000-line home page in one shot before the design language was settled. Build smallest verifiable section first.
- Using `flex items-end` with nested flex-column charts → percentages collapse to 0. The fix is `flex gap-* h-*` (no items-end) and `flex-col justify-end` on each column.
- Putting display text in italic by default. Italic should be earned.
- Designing on warm-paper backgrounds when the user wants white.
- Treating the editorial voice as the design — it's a layer, not the layer.
