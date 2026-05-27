# Tijori IPO Feed: Current Coverage And Requested Additions

Source:

```txt
https://b2b.tijorifinance.com/b2b/v1/in/api/kite-screener/ipo/
```

Checked: 2026-05-27

## Current Feed Shape

The feed returns a JSON array. Current live count: 548 rows.

Top-level fields on every row:

- `compname`
- `isin`
- `keystats`
- `revenue_mix`
- `peers`
- `shareholding`

Current observed coverage:

| Field | Coverage |
|---|---:|
| `compname` | 548 / 548 |
| `isin` | 548 / 548 |
| `keystats.symbol` | 544 / 548 |
| `keystats.sector` | 548 / 548 |
| `keystats.details` | 548 / 548 |
| `keystats.ipo_size` | 548 / 548 |
| `keystats.market_cap` | 548 / 548 |
| `keystats.pe`, `keystats.pb` | 548 / 548 |
| `keystats.sector_pe`, `keystats.sector_pb` | 548 / 548 |
| `keystats.business_perc`, `existing_perc` | 548 / 548 |
| `keystats.business_value`, `existing_value` | 548 / 548 |
| `keystats.financials.yearly_results` | 548 present, 544 with useful data |
| `revenue_mix.revenue_mix.latest_data` | 544 / 548 |
| `peers` | 543 / 548 have at least one peer |
| `shareholding.prom_holding`, `public_holding` | 520 / 548 |

Current pipeline use:

- Writes `data/derived/tijori_ipo_enrichment.json`.
- Writes `data/derived/sector_map.json`.
- Normalization currently uses only the sector map as fallback sector/industry classification.
- The richer facts are preserved in derived data but are not yet rendered on public issue pages.

## Highest-Value Additions

These would make the feed much more useful as an IPOWatch source and reduce
model/PDF dependency for common page facts.

### Identity And Linking

- Stable Tijori company/security ID.
- NSE symbol and BSE scrip code where available.
- Issue type: IPO, FPO, SME IPO, REIT, InvIT, NCD, rights, buyback, OFS.
- Board/platform: mainboard, SME, BSE SME, NSE Emerge, REIT/InvIT.
- Canonical company slug or permalink.
- Document source URL or filing source URL for each row.
- Last updated timestamp per row and per field group.

### IPO Lifecycle

- Filing date.
- DRHP date, RHP date, prospectus date.
- Open date, close date, allotment date, refund/initiation date, credit date, listing date.
- Status enum: filed, upcoming, open, closed, listed, withdrawn.
- Listing exchange(s).

### Offer Structure

- Fresh issue amount in crore as numeric.
- OFS amount in crore as numeric.
- Total issue size in crore as numeric.
- Share count offered.
- Price band lower/upper as numeric rupees.
- Final issue price.
- Face value.
- Lot size.
- Retail minimum investment.
- QIB/NII/retail/employee/shareholder reservation quantities.

### Subscription And Demand

- Latest subscription by category: QIB, NII, retail, employee, shareholder, total.
- Applications count by category.
- Shares offered and shares bid by category.
- Source timestamp for subscription numbers.
- Exchange source: NSE, BSE, consolidated.

### Listing And Market Data

- Listing open, high, low, close.
- Current price.
- Current market cap.
- Issue-price return and current return.
- Data source and timestamp.

### Business And Financials

- Financial units and currency explicitly stated.
- Revenue, EBITDA, PAT, net worth, total borrowings, operating cash flow for at least three fiscal years.
- Restated/consolidated flag.
- Period labels as machine dates, not only `Mar 2025`.
- Revenue mix with segment names, percentages, period date, and units.
- Key business KPIs where available.

### Valuation And Peers

- EPS, NAV, RoNW, market-cap-to-sales.
- P/E and P/B basis: pre-issue or post-issue, diluted or basic.
- Peer identifiers: company name, symbol, ISIN.
- Peer metrics with period labels.

### Ownership And Selling Shareholders

- Pre/post promoter holding.
- Public holding.
- Selling shareholder names.
- Shares/amount sold by each selling shareholder.
- Promoter group flag.

### Risks And Use Of Proceeds

- Objects of the issue with amounts.
- Debt repayment amount.
- Capex amount.
- Working capital amount.
- General corporate purposes amount.
- Top risk factor bullets with prospectus page/source if available.

## Format Preferences

- Prefer numeric fields as numbers, not formatted strings such as `858 Cr` or `₹3,291`.
- Include `currency`, `unit`, and `as_of`/`period_end` fields where relevant.
- Keep display text fields too, but do not make them the only source.
- Include field-level source URLs or source document/page when possible.
- Use stable IDs so we can match rows safely across name changes and re-filings.
