# NSE/BSE Primary Market Coverage Audit

Audit date: 2026-05-25

This note records the current state of IPOWatch V3 ingestion against the live
NSE and BSE primary-market surfaces. The conclusion is deliberately strict:
V3 is strong for equity IPO/FPO records and live subscription data, but it is
not yet complete for the company/security issuance universe of OFS, buybacks,
QIPs, primary debt, InvIT/REIT placements, takeovers, delistings, and
other further-issue categories.

## Current Coverage Summary

Generated V3 public records currently contain:

- IPO: 4,481
- Rights: 281
- OFS: 170
- Buyback: 151
- NCD: 37
- REIT: 9
- InvIT: 8
- FPO: 5
- Others: 54

The current source coverage report says:

- total endpoints: 21,317
- parsed into V3 canonical records: 20,977
- parser failed: 14
- unsupported gaps: 301
- unclassified: 0
- primary-source endpoints: 20,956
- primary blocking gaps after exemptions: 0
- NSE demand endpoints observed: 4
- BSE bid/demand endpoints observed: 16,401

The primary-structure audit command is:

```bash
.venv/bin/python -m ipo_portal.orchestrator audit-source-structure
```

Current report:

```text
data/reports/primary_source_structure_audit.json
```

## Equity IPO/FPO Coverage

This is the best-covered area.

NSE base surfaces fetched:

- current IPOs
- upcoming IPOs
- public past issues
- past security-type helper
- current IPO nested detail page
- current IPO nested bid details
- current IPO consolidated bid details
- current IPO demand data for NSE
- current IPO demand data for all exchanges

BSE base and nested surfaces fetched:

- public issue master
- public issue details
- IPO document list
- BSE performance by year for mainboard and SME
- per-IPO issue detail
- per-IPO category bid details
- per-IPO consolidated bid details
- per-IPO newer consolidated bid details
- per-IPO BSE demand schedule
- per-IPO BSE/consolidated demand graph URLs

Astro V3 consumption now reads:

- `subscription.consolidated`
- `subscription.by_exchange`
- demand curves from V3 trajectories
- BSE demand schedule observations from V3 trajectories

Important caveat: BSE demand graph HTML endpoints are intentionally exempted
because structured BSE demand schedule and bid-detail APIs are preferred. BSE
`demand_schedule_*` endpoints are now normalized into trajectory observations
with category-wise bid quantities.

## NSE Live Page Structure

The current NSE primary-market JavaScript bundle for:

```text
https://www.nseindia.com/market-data/all-upcoming-issues-ipo
```

references these primary-market APIs:

- `/api/ipo-current-issue`
- `/api/all-upcoming-issues?category=ipo`
- `/api/public-past-issues`
- `/api/ipo-past-security-type`
- `/api/live-ofs-active-issues`
- `/api/live-ofs-past-issues`
- `/api/live-ofs-past-issues?index=GENERAL`
- `/api/live-ofs-past-issues?index=RETAIL`
- `/api/liveIppActive-issues`
- `/api/liveIppPast-issues`
- `/api/liveTenderActive-issues`
- `/api/liveTenderPast-issues`
- `/api/liveWatchRights-issues?index=activeIssues`
- `/api/liveWatchRights-issues?index=pastIssues`
- `/api/invits-current-issues`
- `/api/invits-past-issues`
- `/api/reits-current-issues`
- `/api/reits-past-issues`
The live bundle also references endpoints that are not currently in
`ipo_portal/sources.py`:

- `/api/all-upcoming-issues?category=forthcoming`
- `/api/all-upcoming-issues?category=invits`
- `/api/all-upcoming-issues?category=reits`
- `/api/all-upcoming-issues?category=tender`
- `/api/all-upcoming-issues?category=gsec`
- `/api/lwf?type=activeIssues`
- `/api/lwf?type=forthcoming`
- `/api/lwf-past-issues?`
- `/api/lwf-companylist`
- `/api/noncompbid-issue?index=activeissues`
- `/api/noncompbid-issue?index=pastissues`
- `/api/ncbgsec-pastissues?index=pastissues`
- `/api/mfss-new-fund`
- `/api/mfss-other-data`
- `/api/mfss-securities?search=%QUERY`
- `/api/zczp-active-issue`
- `/api/zczp-forthcoming`
- `/api/zczp-past-issue`
- `/api/zczppast-company-name`

Interpretation:

- The IPO, OFS, tender, rights, IPP, InvIT, and REIT page families are in
  scope for IPOWatch primary-market coverage.
- NSE LWF, government securities/non-competitive bidding, MFSS, and ZCZP are excluded
  from IPOWatch V3 scope. They appear on NSE primary-market pages, but they are
  not company IPO/public-issue records for this product.

Current NSE parser status:

- NSE offer-document detail and abridged prospectus section endpoints are
  registered as explicit document metadata feeds. Scalar prospectus facts still
  come from the citation-verified filing intelligence pipeline.

## BSE Live Page Structure

The current BSE public issue application bundle contains a much larger
primary-market surface than V3 currently normalizes. V3 fetches some of these
base endpoints; the fetched document-feed surfaces are now explicitly
registered as document metadata, while deeper action-specific detail endpoints
still need first-class modules.

Covered or mostly covered:

- `GetPublicIssue/w`
- `GetPublicIssue_par/w`
- `Pubissues_IPODRHP_par_ng/w`
- `GetMkt_ISSUE_BBS_IPO/w`
- `Pubissues_GetBkbldgCatdem_ng/w`
- `Pubissues_GetBkbldgCatdem_PAR_ng/w`
- `Pubissues_GetBkbldgCatdem_PAR_bbnew_ng/w`
- `Pubissues_BSEDemandSchedule_otb_ng/w`
- yearly mainboard/SME IPO performance APIs

Fetched and registered as document metadata feeds:

- `Pubissues_BondIssues_DRHP_ng/w`
- `Pubissues_FIS_Buyback_Openmkt_isd_ng/w`
- `Mkt_Pubissues_FIS_BuybackTenderoffer_isd_ng/w`
- `Pubissues_get_InvitPlacement_ng/w`
- `Pubissues_INVSTSandREITS_File_ng/w`
- `Pubissues_FurtherIssuesummary_QIP_isd_ng/w`
- `Pubissues_FurtherIssuesummary_RI_isd_ng/w`
- `Mkt_Pubissues_FIS_Takeover_isd_ng/w`
- `Pubissues_FIS_VoluntaryDelisting_isd_ng/w`

BSE bundle endpoints/families not yet represented fully in the fetch/normalize
contract:

- OFS landing and live detail:
  - `Pubissues_OFSLANDINGPAGE_NEW_ng/w`
  - `Pubissues_OFSLANDINGPAGE_ng/w`
  - `Mkt_CurrDeri_OL_OFS_beta/w`
  - `OL_OFS_ng/w`
  - `OFSDisp_ng/w`
  - `OFSDet_RetNonRet_ng/w`
  - `OFSDet_RetNonRet_T_ng/w`
  - `OFSDet_RetNonRet_T2_ng/w`
- cumulative demand beyond the current bid summary:
  - `Pubissues_BBS_CumultveCatdem_ng/w`
  - `Pubissues_BBS_CumultveCatdem_PAR_ng/w`
  - `Pubissues_BSECumu_Demand_ng/w`
  - `Pubissues_DemSch_GreenShoe_ng/w`
  - `Pubissues_BSEDemSchd_GrShoe_ng/w`
- SME/FPO book-building:
  - `Pubissues_SME_FPO_BW_ng/w`
  - `BSEDemandSchedule_FPO/w`
  - `BSEDemandSchedule_FPO_newformat/w`
- acquisition/open-offer detail:
  - `Pubissues_AcqIssueDetail_BBS_ACQ_ng/w`
  - `Pubissues_AcqIssue_ACQDispDetails_ng/w`
- further issue categories:
  - `Pubissues_FurIssuesumm_ADRGDR_isd_ng/w`
  - `Pubissues_FurIssuesumm_FCCB_isd_ng/w`
  - `Pubissues_FurtherIssuesummary_Pref_isd_ng/w`
  - `Pubissues_FurtherXbrlview_RI_ng/w`
  - `Pubissues_FurtherXbrlview_RI_detailsofobj_ng/w`
  - `Pubissues_FurtherXbrlview_QIP_ng/w`
  - `Pubissues_FurtherXbrlview_QIPdetailsofmerc_ng/w`
  - `Pubissues_FurtherXbrlview_QIPObjectIssue_ng/w`
  - `Pubissues_FurtherXbrlview_FCCB_ng/w`
  - `Pubissues_FurtherXbrlview_FCCB_detailsofobj_ng/w`
  - `Pubissues_FurtherXbrlview_pref_ng/w`
  - `Pubissues_FurtherXbrlview_pref_detailsofobj_ng/w`
  - `Pubissues_FurtherXbrlview_pref_detailsofallotees_ng/w`
- buyback/takeover/delisting phase detail:
  - `Pubissues_FXV_Buyback_openmkt_ng/w`
  - `Pubissues_FXV_Buyback_tenderoffer_ng/w`
  - `Pubissues_FXV_DetailsTakeover_ng/w`
  - `Pubissues_FXV_VDelisting_Pre_ng/w`
  - `Pubissues_FXV_VDelisting_Post_ng/w`
- primary debt:
  - `Pubissues_Bond_Issuances_Fin_Year_ng/w`
  - `Pubissues_Bond_Issuances_Get_COMPANYNAME_ng/w`
  - `Pubissues_Bond_Issuances_Get_BondData_ng/w`
  - `Pubissues_Bond_Issuances_EBP_Dis_New_ng/w`
  - `Pubissues_Bond_Issuances_BondDataNONEBP_ng/w`
  - `Pubissues_Bond_Issuances_NONEBPDATA_Dis_ng/w`
  - `Pubissues_BSE_GetBond_Files_ng/w`
  - export variants for EBP/non-EBP bond data
- InvIT/REIT demand and placement detail:
  - `BSEDemandScheduleInvitBeta/w`
  - `Mkt_INVITS_ng/w`
  - `InvitDetails/w`
- liquidity window:
  - `Pubissues_LiquiditySmartSearch_beta/w`
  - `Pubissues_LiquidityWindowCSVDwnld_ng/w`
- SGB:
  - Excluded from V3 scope. Sovereign Gold Bonds are discontinued and should not
    block canonical IPOWatch coverage.

## Current Answer To "Have We Re-Ingested Everything?"

No.

We have re-ingested a large NSE/BSE primary-source corpus into V3, and the
equity IPO/FPO path is good enough to power public IPO pages with consolidated
subscription and demand data. The fetched primary-source surfaces now have zero
blocking parser gaps after the explicit scope exclusions. The remaining work is
to add deeper BSE endpoint families and first-class per-action modules, not to
fix currently fetched parser failures.

The biggest gaps are:

1. BSE non-IPO primary-market families are fetched as top-level document feeds
   and registered as document metadata: primary debt, buybacks, QIP, rights,
   InvIT/REIT documents, takeovers, and delistings. SGB is intentionally
   excluded.
2. BSE exposes deeper detail APIs for OFS, cumulative demand, green-shoe demand,
   SME/FPO, acquisition/open offers, XBRL further-issue detail, bond issuance
   detail/files, and buyback/takeover/delisting phase detail. These are not yet
   part of the deterministic V3 fetch/normalize contract.
3. NSE ZCZP is intentionally excluded from scope.
4. NSE offer-document detail and abridged prospectus section endpoints are
   fetched and registered, but their scalar facts remain out of the public
   contract unless verified by the filing intelligence pipeline.
5. NSE live page references LWF, government securities/non-competitive bidding,
   and MFSS surfaces, but they are intentionally out of IPOWatch V3 scope.

## Recommended Implementation Order

1. Keep `audit-source-structure --gate` enabled in local/CI validation for the
   current scoped primary-source contract.
2. Add BSE OFS endpoint families and normalize OFS detail/demand into an OFS
   module instead of overloading IPO subscription.
3. Add BSE primary debt schema and fetch chain for EBP/non-EBP bond issuance
   detail and bond files.
4. Add QIP, rights, preferential issue, FCCB, ADR/GDR, buyback, takeover,
   delisting, and InvIT/REIT schemas as first-class primary-action
   records.
