# Audit Findings

Latest V3 audit: 2026-05-26

Command:

```bash
.venv/bin/python scripts/audit_v3_quality.py --site-root data/ipo_watch_v3 --gate
```

Result:

```txt
=== V3 quality audit: 5,202 public issue records ===
No findings.
```

Additional verification:

- `.venv/bin/python -m ipo_portal.orchestrator refresh-daily --skip-enrich`: partially passed. NSE/BSE fetch, Yahoo fallback pricing, normalize, V3 export, and drift scan ran; SEBI failed because `www.sebi.gov.in` DNS resolution failed in the local environment.
- `.venv/bin/python -m ipo_portal.orchestrator audit-source-structure --gate`: passed; current report has 0 blocking primary parser gaps, 4 NSE demand-data endpoints, and 16,401 BSE bid/demand endpoints.
- `.venv/bin/python scripts/audit_prospectus_facts.py --site-root data/ipo_watch_v3 --gate`: passed with 2 pass, 1 review, 0 fail.
- `python3 -m pytest -q --ignore=tests/test_kite_auth.py`: 164 passed.
- `cd web && npm run build`: passed, 1,387 pages built.

Dataset counts:

- Public issues: 5,202
- Review: 2,012
- Quarantined/removed from public indexes: 1,891
- Companies: 4,356
- Trajectories: 220
- Performance rows: 2,486
- Dataset version: `v3.2026.05.26-0338`

Current issue subscription coverage check:

- Latest NSE current IPO snapshot has 0 rows; no active NSE IPO rows were available from NSE in the 2026-05-26 fetch.
- Latest BSE current public issue snapshot has 19 rows.
- V3 has 17 open/upcoming public records.
- 13 of those 17 have normalized `subscription.by_exchange` category data.
- 4 open/upcoming records lack IPO-style normalized subscription categories, now with explicit V3 `subscription.data_availability` reasons:
  `prabha-energy-call-money-7e37ab` and `avg-logistics-5c82c4` are NSE rights rows where the current rights feed publishes offer-window fields while demand fields are null; `shantai-190011` and `garware-technical-fibres-831bde` are BSE OTB/buyback rows where IPO/FPO bid-book category endpoints are not applicable.
- Historical NSE nested issue data is preserved in V3 for `q-line-biotech-7c0bb8` and `bio-medica-laboratories-d385c5`, including `issue_detail`, `bid_details`, `consolidated_bid_details`, and `demand_data`.

Open audit work:

- Add product-specific demand/acceptance parsers for BSE buyback tender rows if IPOWatch should display buyback acceptance progress; the current IPO/FPO subscription book contract is intentionally marked not applicable for those rows.
- Local daily refresh should be rerun once SEBI DNS resolves, or the daily job should treat SEBI as stale-if-fail with an explicit freshness gate.
- Review `data/reports/upstream_drift.jsonl`; the latest full refresh recorded 331 blocking drift events even though the primary source-structure gate passed.
- Add the next layer of BSE primary-action detail endpoints for OFS, green-shoe/cumulative demand, SME/FPO book-building, primary debt, QIP/further-issue XBRL detail, buyback/takeover/delisting phase detail, and InvIT/REIT placement modules.
- Scale verified DeepSeek extraction for recent RHP/DRHP documents and publish only citation-verified facts.
- Expand Yahoo/Kite candle analytics for drawdown and longer-period return quality.
- Decide whether review-state prospectus pages should be indexed or noindexed.
