# Edge cases and contamination catalog

This document is the **canonical spec** for what the v2 normalizer must
defend against. Every contamination pattern below is something we have
either already observed in the live `data/site/` output, seen in raw
NSE/BSE snapshots, or know about from Indian capital-markets practice.

Each entry has the same shape:

* **Problem** — what goes wrong if we do nothing.
* **Trigger** — which source / endpoint surfaces the risk.
* **Mitigation** — what the v2 pipeline must do.
* **Rule ID** — the stable identifier used by `validate_v2.py` so we can
  track recurrence over time. New rules append; we never renumber.

Rule IDs are namespaced by category (`E.ID.*`, `E.CUR.*`, etc.) and zero-
padded so they sort lexically. **Never delete a rule** — mark it
`deprecated_since: <date>` if its trigger goes away.

---

## E.ID — Identifier collisions and stability

### E.ID.001 — Same `source_record_id`, two companies
**Problem.** NSE/BSE occasionally reissue an internal IPO_NO or symbol
after a withdrawal/relisting. Naive joins map the new company onto the
old record, silently corrupting history.
**Trigger.** Any source where `source_record_id` is not globally unique
across time (BSE `IPO_NO`, NSE `symbol`, PRIME `index` slot).
**Mitigation.** Stable join key is `(source, source_record_id,
listing_year)` — never `(source, source_record_id)` alone. On collision
without a year boundary: quarantine the newer row, log to
`reconciliation.jsonl`, alert.

### E.ID.002 — Company name variants ("Ltd" vs "Limited")
**Problem.** "Kalana Ispat Ltd" and "Kalana Ispat Limited" are the same
entity; if we slug them independently we double-count companies and
fragment performance data.
**Trigger.** Already producing 873 warnings against the v1 pipeline.
Sources: NSE offer documents, BSE public issues, PRIME demos.
**Mitigation.** Slug normalization: strip suffix tokens (`Ltd`,
`Limited`, `Pvt`, `Private`, `Inc`, `Corp`, `Corporation`, `Co`,
`Company`, `&`, `and`), collapse whitespace, lowercase, strip
punctuation. Then a Levenshtein-distance gate (≤2 on normalized form)
flags potential merges to a reviewer queue — we do **not** auto-merge
above L=0 without a second match key (ISIN, PAN, registrar).

### E.ID.003 — PAN reuse / typos
**Problem.** Company PAN is supposed to be unique, but BSE has been
observed publishing the wrong PAN on a handful of rows. A bad PAN join
collapses two distinct companies into one.
**Trigger.** NSE `pan_no`, BSE PAN fields.
**Mitigation.** Require PAN to match a structural regex
(`^[A-Z]{5}[0-9]{4}[A-Z]$`) AND co-occur with a matching name (after
normalization from E.ID.002). PAN-only joins are forbidden.

### E.ID.004 — Symbol reuse after delisting
**Problem.** NSE/BSE symbols are reusable. "ABCD" listed in 2009 and
"ABCD" listed in 2024 may be different companies.
**Trigger.** NSE `symbol`, BSE `Scrip_cd` lookups against historical
data.
**Mitigation.** Symbol-only joins are forbidden when both sides span
more than 60 days of listing-date difference. Always pair with another
identifier (ISIN, PAN, or company name fuzzy match).

### E.ID.005 — Slug stability after rename
**Problem.** Companies change name (merger, rebrand). If our slug is
name-derived, the URL breaks; if it's id-derived, it loses meaning.
**Trigger.** Long-tail issue.
**Mitigation.** Slug is `<normalized-name>-<short-id>` where `short-id`
is a 6-char sha1 prefix of the stable join key. Renames preserve the
short-id, change only the name segment. The `aliases[]` array records
all previous slugs and 301-redirects them.

### E.ID.006 — Dual-listed mainboard IPOs
**Problem.** An IPO listing on both NSE and BSE shows up twice — once
per exchange feed — and our v1 pipeline does not always merge them.
**Trigger.** Any mainboard IPO. Visible on the live `/upcoming/` page.
**Mitigation.** Canonical issue = `(ISIN OR (normalized_name +
listing_date_window±3d))`. Both exchange rows attach to the same
canonical issue via `exchange_listings[]`. Subscription metrics from
both exchanges are aggregated under a documented rule (see E.SUB.002).

---

## E.CUR — Currency, units, magnitude

### E.CUR.001 — ₹ in lakhs vs crores vs rupees
**Problem.** Issue size on NSE is sometimes rupees, on BSE sometimes
lakhs, in news feeds sometimes "₹500 Cr" text. Off by 10⁵ or 10⁷ if
misread.
**Trigger.** Issue size fields, listing-day amount fields, almost all
monetary fields.
**Mitigation.** Internal canonical unit is **paise** (₹ × 100). Every
parser annotates its detected unit at the boundary and converts. Storing
INR as int paise also fixes float-precision issues (E.NUM.001).
Conversion lookup:
* `"Rs. 1.5 Cr"` → 15_00_00_000 ₹ → 1_500_000_000 paise
* `"1500 lakhs"` → 15_00_00_000 ₹ → 1_500_000_000 paise
* `15_00_00_000` (BSE int) → 1_500_000_000 paise

### E.CUR.002 — Mixed scale within one row
**Problem.** BSE `PublicIssue_par` returns issue size as integer rupees,
but `lot_size` in shares, and `issue_price` in rupees-per-share —
parsers that pick a global unit get one column wrong.
**Trigger.** BSE public issue details, PRIME demo rows.
**Mitigation.** Per-field unit annotation in the catalog (Phase 1) and
per-field conversion in the normalizer. Never assume a row-wide unit.

### E.CUR.003 — Negative listing prices on circuit
**Problem.** When an IPO closes locked at upper or lower circuit, some
feeds return `0`, `-1`, or `null` for close price. Treating these as
real values produces 100% loss / infinite gain on the dashboard.
**Trigger.** Listing-day data from Kite, BSE, NSE.
**Mitigation.** A close of exactly `0` or negative is treated as null;
record `listing_day_circuit_locked: true` and source the price from the
next trading day if available.

---

## E.DAT — Dates and times

### E.DAT.001 — `dd-MMM-yyyy` parsing
**Problem.** NSE and PRIME return dates as `"21-May-2026"`. A naive
ISO-parser fails or worse, locale-dependent parsing gives wrong months.
**Trigger.** NSE `issueStartDate`, `issueEndDate`, `listingDate`; PRIME
demo coverage rows.
**Mitigation.** Single parser `parse_indian_date()` in `units.py`
handles `dd-MMM-yyyy`, `dd/MM/yyyy`, `dd-MM-yyyy`, `YYYY-MM-DD`,
`YYYY/MM/DD`. Months recognized case-insensitive. Two-digit years
rejected with a blocking validation error (E.DAT.001 rule_id).

### E.DAT.002 — Timezone-naive instants
**Problem.** BSE bid-snapshot timestamps come without timezone but are
IST. If we treat them as UTC, they appear 5h30m in the past, breaking
hourly subscription trajectories.
**Trigger.** All BSE timestamp fields, NSE bid-details, trajectory
snapshots.
**Mitigation.** All timestamps stored as ISO 8601 with explicit offset.
A bare timestamp from NSE/BSE is parsed as `Asia/Kolkata` then converted
to UTC. The conversion is logged once per source to `freshness.<source>`
so consumers can verify.

### E.DAT.003 — Date revisions (postponements)
**Problem.** IPO open/close dates get revised. A naive fetcher
overwrites the prior date and loses the audit trail.
**Trigger.** Any active IPO whose dates change between snapshots.
**Mitigation.** Date fields are versioned in
`data/site_v2/audit/<slug>/dates.jsonl` — every change appends a row
with `observed_at`, `field`, `old`, `new`. The canonical record always
shows the latest; the history is exposed at `/api/<slug>/history`.

### E.DAT.004 — Listing-day vs allotment-day
**Problem.** "Listing date" can mean credit-to-demat (T+3 in current
T+3 cycle) or actual exchange listing (T+5). Sources differ.
**Trigger.** Cross-source listing-date reconciliation.
**Mitigation.** Field is `exchange_listing_date` explicitly; the
allotment date is `allotment_date`. Never alias.

### E.DAT.005 — Weekend / holiday adjustments
**Problem.** An IPO scheduled to close on a Saturday may auto-extend to
Monday; sources don't always reflect this in real time.
**Trigger.** Active IPOs near weekends and public holidays.
**Mitigation.** Validation rule cross-references a static NSE trading-
holidays list (refreshed annually from NSE circular). Closing dates
that fall on holidays raise a `warning` and a `expected_close_actual`
field is computed.

---

## E.NUM — Number parsing

### E.NUM.001 — Float precision on subscription multiples
**Problem.** `0.87x` parsed as float becomes `0.86999...` and `18.235x`
becomes `18.23` after rounding for display.
**Trigger.** All subscription `times` fields, gain percentages.
**Mitigation.** Subscription `times` stored as `Decimal` with 4 places.
Currency stored as int paise (E.CUR.001). Percentages stored as int
basis-points (1% = 100 bps).

### E.NUM.002 — Indian numbering format
**Problem.** `"1,00,000"` (1 lakh) vs `"100,000"` (100 thousand) — both
appear in feeds.
**Trigger.** Mostly news feeds and HTML scrapes; rarely in JSON APIs.
**Mitigation.** `parse_indian_number()` strips all commas regardless of
position. Validation cross-checks magnitude against the expected
order-of-magnitude for the field (e.g., a "lot size" > 50_000 is
flagged for review).

### E.NUM.003 — Numbers as strings
**Problem.** NSE returns `"noOfSharesOffered": "3772000"` — a string.
Downstream consumers expecting `number` break.
**Trigger.** Most NSE list endpoints.
**Mitigation.** Catalog-driven coercion: every field with `type:
"integer"` or `"number"` in the catalog is coerced via
`coerce_number()` which handles strings, commas, whitespace, and
sentinel values (`"-"`, `"NA"`, `"N/A"`, `""`).

---

## E.SUB — Subscription / bid book

### E.SUB.001 — Anchor vs total QIB double-count
**Problem.** Anchor allocation is part of QIB but reported separately;
naive sum of categories double-counts anchor shares.
**Trigger.** BSE `consolidated_bid_details_new_*`, NSE
`ipo-active-category`.
**Mitigation.** Canonical field is `qib_excluding_anchor` and `anchor`
separately. The displayed "QIB" figure is `qib_excluding_anchor +
anchor`. Reconciliation check: `qib_total == qib_excluding_anchor +
anchor ± 0.01`.

### E.SUB.002 — NSE vs BSE subscription disagreement
**Problem.** NSE and BSE report subscription independently; their `times`
values differ by 1-5%. Which is canonical?
**Trigger.** Dual-listed IPOs.
**Mitigation.** Both stored under
`subscription.by_exchange.{nse,bse}.times`. The headline
`subscription.times` field is computed from the larger total bid
volume / aggregated offered shares, with the formula documented in
`docs/data/SCHEMA_GUIDE.md`. Discrepancies > 10% raise a warning.

### E.SUB.003 — Gross vs net of anchor for `times`
**Problem.** Some sources report subscription as a multiple of the gross
offer; others as a multiple of the net offer (i.e., excluding anchor).
**Trigger.** Mainboard IPOs with anchor allocation.
**Mitigation.** Canonical `subscription.times` is always **net of
anchor**. `subscription.times_gross` is also stored for reference.

### E.SUB.004 — Empty trajectory after close
**Problem.** Trajectories should freeze at close + 7 days. If a fetcher
re-runs against a closed IPO, we risk overwriting frozen data.
**Trigger.** Already in v1; carrying forward to v2.
**Mitigation.** Trajectory writer reads existing file's
`frozen_at`; if set, refuses to write unless an explicit `--force-thaw`
flag is passed. Frozen instances are flagged in the manifest.

---

## E.SRC — Source merging and precedence

### E.SRC.001 — NSE/BSE active-issue duplicates
**Problem.** A mainboard IPO appears on both NSE and BSE upcoming
feeds; v1 emits two upcoming rows on the home page.
**Trigger.** Live observation in production.
**Mitigation.** See E.ID.006 — canonical issue dedup at normalization
time, not at render time.

### E.SRC.002 — Source precedence rules
**Problem.** When two sources disagree on a field value, which wins?
**Trigger.** All multi-source fields.
**Mitigation.** Explicit precedence rules in
`docs/data/SOURCE_PRECEDENCE.yaml` keyed by field path. Default tier
order: `exchange_of_listing > other_exchange > prime > trendlyne >
moneycontrol > kite`. Each applied decision is recorded in
`field_provenance[]` on the record.

### E.SRC.003 — Trendlyne enrichment masking primary data
**Problem.** v1 sometimes lets Trendlyne fill in fields where NSE/BSE
returned null; if the primary later updates, Trendlyne's value sticks.
**Trigger.** Listing performance fields.
**Mitigation.** Enrichment-tier sources can only write to a field if no
primary or secondary source has *ever* successfully reported it. On
primary report, enrichment values are evicted and the field is
re-populated from primary; the eviction is logged.

---

## E.STA — Status / state machine

### E.STA.001 — Withdrawn IPOs treated as issued
**Problem.** An IPO can be withdrawn after RHP filing but before
allotment; counting it in "issues issued" inflates totals.
**Trigger.** SEBI rejection notices, voluntary withdrawal.
**Mitigation.** Status enum:
`upcoming | active | closed | listed | withdrawn | rejected | postponed`.
Aggregations explicitly include/exclude per-status. The home-page
"issues issued this year" counter uses `listed` only.

### E.STA.002 — OFS conflation with IPO
**Problem.** Offer-for-sale (OFS) is a sale by existing shareholders,
not new issuance. v1 sometimes mixes them into IPO counts.
**Trigger.** NSE has separate endpoints but the front-end pages mix.
**Mitigation.** `issue_kind` enum: `ipo_mainboard | ipo_sme | ofs |
rights | buyback | qip | ipp | invit | reit | sgb | ncb | other`.
Aggregations key off `issue_kind`, never off `issue_type` strings from
upstream.

### E.STA.003 — Listed-but-immediately-suspended
**Problem.** Rare but real: an IPO lists then gets suspended same day.
Listing-day performance is null/wrong.
**Trigger.** SEBI/exchange suspension actions.
**Mitigation.** Listed status with `trading_suspended: true` flag.
Performance fields gain a `suspended_at` annotation rather than null
fills.

---

## E.HTM — HTML / encoding / locale

### E.HTM.001 — HTML in document description fields
**Problem.** BSE document feeds sometimes embed HTML (`<a>`, `&nbsp;`,
`<br>`) in description strings; rendering them raw is XSS risk and
display corruption.
**Trigger.** BSE `*_documents` endpoints.
**Mitigation.** `sanitize_plaintext()` strips all tags, decodes HTML
entities, normalizes whitespace. Stores the original under
`raw_description` for audit only.

### E.HTM.002 — Non-ASCII company names
**Problem.** Hindi / Tamil characters in company names appear in some
endpoints; Unicode normalization (NFC vs NFD) drift breaks string
matches.
**Trigger.** Any company-name field.
**Mitigation.** All strings normalized to NFC at the boundary.
Slugification uses unidecode for stable ASCII slug, but the canonical
display name preserves original Unicode.

### E.HTM.003 — BOM, CRLF, trailing whitespace
**Problem.** Some CSV/HTML scrapes leave BOM markers and `\r` in
strings; equality comparisons fail invisibly.
**Trigger.** Capitalmarket HTML scrapes.
**Mitigation.** `clean_text()` strips BOM, normalizes line endings to
LF, trims whitespace.

---

## E.SNP — Snapshot lifecycle / stale data

### E.SNP.001 — Empty body sentinel mistaken for "no data"
**Problem.** A successful fetch returning `[]` (2 bytes) is
indistinguishable from a silent-fail returning `[]`. After 24h of fails
we'd publish "no upcoming IPOs" when there are several.
**Trigger.** Any endpoint that legitimately returns empty arrays.
**Mitigation.** Per-source `expected_empty_endpoints` whitelist —
empty bodies are only "fine" for endpoints we expect to be empty during
off-cycle (e.g., `lwf_active` outside an LWF window). All other empty
bodies raise a `staleness_warning` after N consecutive empty results
(configurable per source, default N=3).

### E.SNP.002 — Stale snapshots silently aging
**Problem.** Cron fails silently; last successful snapshot is 7 days
old; we keep publishing it.
**Trigger.** GitHub Actions runner outages, NSE rate limiting.
**Mitigation.** Per-source `staleness_tolerance_hours` configured in
`docs/data/SOURCES.md`. If `now - last_success > tolerance`, the
manifest writes a `stale_sources[]` array and the dataset version
includes a `degraded:true` flag. Consumers can refuse to ingest
degraded datasets.

### E.SNP.003 — Cache poisoning
**Problem.** Upstream serves bad data once; our hash-gated writes
preserve it indefinitely.
**Trigger.** Any source.
**Mitigation.** Daily integrity job re-fetches a sample (random 5%)
without the hash gate and compares. Mismatches log to
`data/reports/integrity.jsonl`.

---

## E.PAG — Pagination and dropdown traps

### E.PAG.001 — ASP.NET form-state drift
**Problem.** Capitalmarket pagination uses `__VIEWSTATE` posts; pages
can shift between fetches as new rows insert.
**Trigger.** Capitalmarket `ipo_historic_table_page_*`.
**Mitigation.** Snapshot the `index` page before paging; record
`expected_page_count` and `top_row_signature` per page. If
re-paginating produces a different signature, redo from page 1.

### E.PAG.002 — Dropdown-dependent data
**Problem.** BSE OFS data is keyed by a date dropdown; iterating the
dropdown is required to get full data.
**Trigger.** BSE `ofs_date_list`.
**Mitigation.** Catalogued in Phase 1 with `dropdown_dependent: true`.
Phase 5 (gap-scan) generates a per-dropdown-value endpoint list and
schedules fetches.

### E.PAG.003 — Stop-condition off-by-one
**Problem.** Moneycontrol pagination stops at the first short page;
but the last short page contains 1-19 valid rows we need.
**Trigger.** Moneycontrol listed_ipos pagination.
**Mitigation.** Stop only on **two consecutive** short pages, and
record the last short page's contents.

---

## E.UPS — Upstream schema drift

### E.UPS.001 — New fields added silently
**Problem.** NSE/BSE add fields without notice; we miss them until
manual inspection.
**Trigger.** Any source.
**Mitigation.** `orchestrator/drift.py` runs after every snapshot
collection: compares the live snapshot's leaf-field set against the
canonical catalog. New fields → `upstream_drift.jsonl` + Slack/email
hook (TBD).

### E.UPS.002 — Field renames
**Problem.** A field is renamed (e.g., `pan_no` → `companyPan`); our
parser still references the old name and silently nulls.
**Trigger.** Any source rename.
**Mitigation.** Each catalog entry's `path` is checked on every fetch;
disappearance of an expected path raises a blocking error. The
normalizer maintains an `alias_map` so renames can be patched without
losing historical data.

### E.UPS.003 — Type changes
**Problem.** A field changes from string-int (`"3772000"`) to number
(`3772000`); the parser's coerce step happens to handle both but
downstream consumers may not.
**Trigger.** NSE has done this in the past.
**Mitigation.** Catalog records observed `type` per field; drift
detector flags type changes for review even if `coerce_number()`
absorbs them silently.

---

## E.LEG — Regulatory / legal

### E.LEG.001 — Pre-listing publicity rules
**Problem.** SEBI restricts what can be published during the silent
period (RHP to listing). Publishing GMP, projections, or unofficial
allotment data before SEBI permits may be a regulatory issue.
**Trigger.** Any field sourced from grey-market or unofficial channels.
**Mitigation.** GMP / grey market data is **not** published in v2.
Phase 6 (RHP enrichment) extracts only what is in the public RHP.

### E.LEG.002 — Personally identifiable information
**Problem.** Some sources include promoter PAN, director DIN, contact
emails. Public republishing without aggregation may breach DPDP Act.
**Trigger.** PRIME, some BSE document feeds.
**Mitigation.** PII fields whitelisted explicitly in
`docs/data/PII_POLICY.md`; everything else is redacted at normalization
time.

---

## Adding a new rule

1. Pick the next available `<CATEGORY>.<NNN>` ID.
2. Add the four sections (Problem / Trigger / Mitigation / Rule ID).
3. Update `validate_v2.py` with the check.
4. Update `CHANGELOG.md`.
5. Never reuse a deprecated ID — append `_deprecated_<date>` to retire.
