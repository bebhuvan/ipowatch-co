# The IPO Watch page — what data we display

This is the **page-data contract** for an IPO Watch IPO page. Every
section below lists the data we want to render. The Phase 6 extraction
pipeline (`ipo_portal/orchestrator/rhp_enrich.py`) is designed
specifically to fill this contract from the RHP / DRHP PDF — so the
schema drives the prompts, not the other way around.

The differentiator
------------------
Every other Indian IPO site has either (a) sparse exchange-feed data or
(b) generic AI-written summaries. IPO Watch wins by extracting
**verifiable, page-cited, table-verbatim** data from the actual
prospectus. Each field carries a `provenance` envelope:

```jsonc
{
  "value": "...",            // the extracted value
  "raw_excerpt": "...",      // verbatim quote, ≤500 chars
  "source_page": 247,        // page in the RHP PDF
  "source_section": "Risk Factor 3.2",
  "confidence": "high"       // high | medium | low
}
```

This lets the rendered page display the value AND a tooltip / footnote
showing the source — readers can verify any number we publish against
the actual filing.

Top-level shape
---------------
```
ipo_page_data
├── hero                    — quick stats above the fold
├── company_about           — who they are, business one-liner, history
├── the_offer               — issue mechanics, use of proceeds
├── industry_landscape      — market size, growth, drivers, competition
├── macros                  — policy tailwinds, regulatory regime
├── business_model          — revenue, customers, geographies, moats
├── financial_highlights    — multi-year P&L, BS, ratios, segments
├── valuation               — peer comp, implied multiples
├── risks                   — categorized risk factors with summaries
├── promoter_and_shareholding — pre/post, lock-ins, top holders
├── litigation              — outstanding cases, regulatory actions
├── timeline                — every date with annotation
├── capital_structure       — pre/post paid-up, dilution
├── documents               — RHP, DRHP, prospectus, basis-of-allotment
└── meta                    — extraction provenance, freshness
```

---

## 1. `hero` — fold-line quick stats

```jsonc
{
  "headline_pitch": { "value": "Pan-India distributor of dental supplies and equipment.", "provenance": {...} },
  "company_name": "Vasa Denticity Limited",
  "logo_url": null,
  "issue_price_band_inr_text": "₹121 – ₹128",
  "lot_size_shares": 1000,
  "minimum_investment_inr_text": "₹128,000",
  "open_date": "2023-06-23",
  "close_date": "2023-06-27",
  "listing_date": "2023-07-05",
  "exchange_listings": ["NSE Emerge"],
  "issue_size_inr_text": "₹54.07 Cr",
  "issue_type_label": "Fresh + OFS",
  "subscription_status_text": "Subscribed 191.93x",  // live; sourced from exchange
  "listing_gain_text": "+95.31%"  // post-listing only
}
```

## 2. `company_about`

```jsonc
{
  "legal_name": {...},
  "trade_names": [...],
  "registered_office": {...},
  "cin": "...",
  "incorporation_date_iso": "...",
  "sector": "...",                     // canonical sector enum
  "sub_sector": "...",
  "core_business_one_line": {...},
  "core_business_paragraph": {...},    // 3-5 sentences VERBATIM from RHP "Our Business" intro
  "history_timeline": [
    {"year": 2017, "event": "Company incorporated", "provenance": {...}},
    {"year": 2019, "event": "Launched B2B platform", "provenance": {...}}
  ],
  "promoters": [
    {"name": "...", "designation": "...", "background_one_line": "...", "stake_pre_offer_pct_bps": 6250}
  ],
  "key_management": [
    {"name": "...", "designation": "MD & CEO", "tenure_years": 7, "background_one_line": "..."}
  ],
  "products_services": [
    {"name": "...", "share_of_revenue_pct_bps": 4500, "description_one_line": "..."}
  ]
}
```

## 3. `the_offer`

```jsonc
{
  "issue_type": "Fresh + OFS | Fresh | OFS",
  "is_book_built": true,
  "fresh_issue": {
    "shares": 3174000, "amount_paise": 406272000000, "amount_inr_text": "₹40.63 Cr"
  },
  "ofs": {
    "shares": 1050000, "amount_paise": 134400000000, "amount_inr_text": "₹13.44 Cr",
    "selling_shareholders": [
      {"name": "...", "shares": 350000, "amount_paise": 44800000000}
    ]
  },
  "total_offer": {
    "shares": 4224000, "amount_paise": 540672000000, "amount_inr_text": "₹54.07 Cr"
  },
  "reservation": {
    "qib_shares": 845000,
    "nii_shares": 633000,
    "retail_shares": 2110000,
    "employee_shares": 0,
    "shareholder_shares": 0,
    "policyholder_shares": 0
  },
  "anchor_book": {
    "anchor_date_iso": "...",
    "anchor_portion_paise": ..., "anchor_shares": ...,
    "investor_count": 12,
    "top_investors": [
      {"name": "...", "shares": ..., "amount_paise": ..., "category": "mutual_fund"}
    ],
    "marquee_count": 3,        // count of well-known anchors (e.g., Fidelity, BlackRock)
    "anchor_quality_score_inferred": "high | medium | low"  // editorial inference
  },
  "use_of_proceeds": [
    {"purpose": "Funding working capital requirements", "amount_paise": ..., "percent_bps": 4500, "provenance": {...}},
    {"purpose": "General corporate purposes", "amount_paise": ..., "percent_bps": 2000, "provenance": {...}}
  ],
  "lead_managers": [
    {"name": "Beeline Capital Advisors", "role": "BRLM", "track_record_count": 12}
  ],
  "registrar": {
    "name": "Bigshare Services Pvt Ltd",
    "address": "...", "email": "...", "phone": "...", "website": "..."
  }
}
```

## 4. `industry_landscape`

```jsonc
{
  "industry_name": "Indian dental equipment and consumables",
  "market_size": {
    "current_paise": ..., "current_text": "₹4,500 Cr in FY24", "provenance": {...},
    "projected_paise": ..., "projected_text": "₹7,800 Cr by FY27", "provenance": {...},
    "cagr_bps": 1850, "cagr_period": "FY24-FY27"
  },
  "global_context": {
    "global_market_text": "...", "india_share_text": "...", "provenance": {...}
  },
  "key_drivers": [
    {"driver": "Rising dental awareness in Tier 2-3", "provenance": {...}}
  ],
  "key_headwinds": [...],
  "competitive_landscape": [
    {
      "competitor": "...",
      "type": "listed | unlisted | mnc | fragmented",
      "revenue_paise": ...,
      "market_share_bps": ...,
      "notes": "..."
    }
  ],
  "category_breakdown": [
    {"category": "Equipment", "market_share_pct_bps": 4000},
    {"category": "Consumables", "market_share_pct_bps": 6000}
  ],
  "geographic_breakdown": [...]
}
```

## 5. `macros`

```jsonc
{
  "policy_tailwinds": [
    {"policy": "Ayushman Bharat dental coverage expansion", "year": 2024, "provenance": {...}}
  ],
  "regulatory_landscape": {
    "summary": "...", "provenance": {...},
    "key_regulators": ["CDSCO", "Dental Council of India"]
  },
  "economic_sensitivity": "low | medium | high",
  "rationale": "..."
}
```

## 6. `business_model`

```jsonc
{
  "revenue_model": {"summary": "B2B + D2C dental supplies via online platform.", "provenance": {...}},
  "customer_segments": [
    {"segment": "Dental clinics", "share_of_revenue_bps": 7500},
    {"segment": "Dental colleges", "share_of_revenue_bps": 2500}
  ],
  "customer_concentration": {
    "top_5_share_bps": ..., "top_10_share_bps": ..., "notes": "..."
  },
  "geographic_distribution": [
    {"region": "South India", "share_of_revenue_bps": 4000}
  ],
  "supplier_concentration": {...},
  "competitive_moats": [
    {"moat": "...", "evidence": "...", "provenance": {...}}
  ],
  "key_dependencies": [...]
}
```

## 7. `financial_highlights`

```jsonc
{
  "fiscal_periods": [
    {
      "period": "FY24",
      "period_end": "2024-03-31",
      "revenue_paise": ..., "revenue_inr_text": "₹95.20 Cr",
      "revenue_growth_yoy_bps": 4200,
      "ebitda_paise": ..., "ebitda_margin_bps": 1450,
      "pat_paise": ..., "pat_margin_bps": 980,
      "eps_paise": ...,
      "total_assets_paise": ...,
      "total_debt_paise": ..., "debt_to_equity_x": "0.42",
      "net_worth_paise": ...,
      "roe_bps": 2230, "roce_bps": 2640,
      "cfo_paise": ...,
      "provenance": {...}
    }
  ],
  "revenue_cagr_bps": 4500, "revenue_cagr_period": "FY22-FY24",
  "pat_cagr_bps": 6200,
  "ebitda_cagr_bps": 5800,
  "segment_breakdown": [
    {"period": "FY24", "segment": "Equipment", "revenue_paise": ..., "share_bps": ...}
  ],
  "geographic_revenue": [...],
  "key_observations": ["<one-line>", "..."]  // LLM-extracted narrative bullets
}
```

## 8. `valuation`

```jsonc
{
  "post_offer_paid_up_paise_at_upper_band": ...,
  "implied_market_cap_at_upper_band_paise": ...,
  "implied_market_cap_inr_text": "₹540 Cr",
  "implied_pe_x_at_upper_band": "28.4",
  "implied_pb_x_at_upper_band": "5.2",
  "ev_to_ebitda_at_upper_band": "21.7",
  "peer_comparison": [
    {
      "company": "...",
      "revenue_paise": ..., "pat_margin_bps": ...,
      "pe_x": "...", "pb_x": "...",
      "market_cap_paise": ..., "provenance": {...}
    }
  ],
  "valuation_band_inference": "premium | in-line | discount",
  "rationale": "..."  // LLM editorial sentence
}
```

## 9. `risks`

```jsonc
{
  "total_disclosed_count": 47,
  "by_category": [
    {
      "category": "Operational",
      "count": 12,
      "top_risks": [
        {
          "title": "Single-warehouse dependency",
          "summary": "All inventory is held at one warehouse in Delhi NCR; any disruption affects national distribution.",
          "severity_inferred": "high",
          "risk_factor_number": "3.2",
          "provenance": {...}
        }
      ]
    },
    {"category": "Financial", "count": ..., "top_risks": [...]},
    {"category": "Regulatory", "count": ..., "top_risks": [...]},
    {"category": "Market", "count": ..., "top_risks": [...]},
    {"category": "Geographic", "count": ..., "top_risks": [...]},
    {"category": "Other", "count": ..., "top_risks": [...]}
  ],
  "qualitative_themes": ["Heavy reliance on imports for high-margin SKUs", "..."]
}
```

## 10. `promoter_and_shareholding`

```jsonc
{
  "promoter_holding": {
    "pre_offer_pct_bps": 8500,
    "post_offer_pct_bps": 6230,
    "lock_in_period_text": "3 years for 20% promoter contribution, 1 year for balance"
  },
  "top_public_shareholders": [
    {"name": "...", "stake_bps": 850, "category": "PE | VC | strategic | individual"}
  ],
  "shareholding_pattern": [
    {"period": "Pre-offer", "promoter_bps": 8500, "public_bps": 1500},
    {"period": "Post-offer", "promoter_bps": 6230, "public_bps": 3770}
  ],
  "investor_pedigree_one_line": "..."
}
```

## 11. `litigation`

```jsonc
{
  "tax_disputes": {"outstanding_paise": ..., "count": ..., "notes": "..."},
  "civil_cases": {"outstanding_paise": ..., "count": ..., "notes": "..."},
  "criminal_proceedings": {"count": ..., "notes": "..."},
  "regulatory_actions": {"count": ..., "notes": "..."},
  "material_litigation": [
    {"party": "...", "forum": "...", "amount_paise": ..., "status": "...", "provenance": {...}}
  ]
}
```

## 12. `timeline`

```jsonc
{
  "drhp_filed_iso": "...",
  "rhp_filed_iso": "...",
  "anchor_date_iso": "...",
  "open_date_iso": "...",
  "close_date_iso": "...",
  "allotment_date_iso": "...",
  "refund_date_iso": "...",
  "listing_date_iso": "...",
  "lock_in_expiry_promoter_iso": "...",
  "lock_in_expiry_other_iso": "..."
}
```

## 13. `capital_structure`

```jsonc
{
  "face_value_paise": 1000,
  "pre_offer_paid_up_capital_paise": ...,
  "post_offer_paid_up_capital_paise": ...,
  "dilution_pct_bps": ...,
  "authorised_capital_paise": ...
}
```

## 14. `documents`

```jsonc
{
  "drhp": {"url": "...", "filed_date_iso": "...", "version": 1},
  "rhp": {"url": "...", "filed_date_iso": "...", "version": 1},
  "final_prospectus": {"url": "...", "filed_date_iso": "..."},
  "basis_of_allotment": {"url": "...", "published_date_iso": "..."},
  "public_advertisements": [...]
}
```

## 15. `meta` — extraction provenance & freshness

```jsonc
{
  "rhp_pdf_sha256": "...",
  "rhp_pdf_url": "...",
  "extraction_method": "deepseek-multipass-v1",
  "extracted_at_iso": "...",
  "extraction_passes": [
    {"section": "front_matter", "model": "deepseek-chat", "tokens_in": ..., "tokens_out": ..., "cost_usd": ...}
  ],
  "total_cost_usd": ...,
  "fields_with_high_confidence_count": ...,
  "fields_with_medium_confidence_count": ...,
  "fields_null_count": ...,
  "raw_text_chars": ...,
  "pdf_pages": ...
}
```

---

## Implementation rules

1. **Schema first, prompts second.** The DeepSeek extraction prompts are
   generated from this schema (one targeted prompt per top-level
   section).
2. **Provenance per leaf.** Every extracted leaf carries
   `{value, raw_excerpt, source_page, source_section, confidence}`.
   Renders on the page can show "Source: RHP p.247".
3. **Verbatim over paraphrase.** When the RHP states something cleanly,
   we quote it. The LLM does NOT rewrite financial figures or risk
   summaries.
4. **Null > guess.** If a field can't be located in the supplied slice,
   the value is null and `warnings[]` records why. Never fabricate.
5. **Multi-pass.** Front matter / risks / industry+business /
   financials / valuations+peers / litigation / shareholding — one
   targeted DeepSeek call per section, then a final synthesis pass.
6. **All money in paise, all percentages in bps**, per
   `docs/decisions/001-canonical-storage-units.md`.
7. **Output goes to** `data/site_v2/issues/<slug>/prospectus.json` and
   is referenced by `data/site_v2/issues/by-slug/<slug>.json` via
   `meta.prospectus_extract`.

---

## What the page renders

* Hero card with quick stats + verified subscription status
* "About" with the verbatim business-intro paragraph + a quotable
  one-liner
* Use-of-proceeds donut with a "Source: RHP p.X" footnote per slice
* Industry market-size + CAGR with the verbatim quote in a tooltip
* Multi-year P&L table with audit-stamp provenance per row
* Peer-comparison matrix with peer financials lifted verbatim
* Top risks listed by category with severity indicator + page citation
* Anchor-investor table with pedigree tags ("MF", "FII")
* Promoter holding pre/post with lock-in note
* Litigation summary with outstanding-amount totals

Everything else (subscription trajectory, listing-day performance,
current quote) flows from `data/site_v2/issues/by-slug/<slug>.json` —
the v2 exchange-feed normalized record.
