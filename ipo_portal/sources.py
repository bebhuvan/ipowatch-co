from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable

from .http import HttpClient, HttpResult


NSE_REFERER = "https://www.nseindia.com/market-data/all-upcoming-issues-ipo"
NSE_OFS_REFERER = "https://www.nseindia.com/market-data/public-issues-offer-for-sale-ofs"
NSE_TENDER_REFERER = "https://www.nseindia.com/market-data/public-issues-tender"
NSE_OFFER_DOCS_REFERER = "https://www.nseindia.com/companies-listing/corporate-filings-offer-documents"
NSE_PUBLIC_ADS_REFERER = "https://www.nseindia.com/companies-listing/corporate-public-issue-advertisements"
BSE_REFERER = "https://www.bseindia.com/publicissue"
ABRIDGED_PROSPECTUS_TYPES = (
    "GENERAL",
    "OFFER_PUBLIC",
    "PRICE_BAND",
    "BRLM",
    "REGISTRAR",
    "ISSUER_COMP",
    "OBJ_ISSUE",
    "SHP",
)


@dataclass(frozen=True)
class Endpoint:
    source: str
    name: str
    url: str
    referer: str
    expect_json: bool = True


def nse_endpoints() -> list[Endpoint]:
    base = "https://www.nseindia.com"
    return [
        Endpoint("nse", "ipo_current_issue", f"{base}/api/ipo-current-issue", NSE_REFERER),
        Endpoint("nse", "ipo_upcoming", f"{base}/api/all-upcoming-issues?category=ipo", NSE_REFERER),
        Endpoint("nse", "ipo_public_past_issues", f"{base}/api/public-past-issues", NSE_REFERER),
        Endpoint("nse", "ipo_past_security_type", f"{base}/api/ipo-past-security-type", NSE_REFERER),
        Endpoint("nse", "ofs_active_retail", f"{base}/api/live-ofs-active-issues-ss?index=RS", NSE_OFS_REFERER),
        Endpoint("nse", "ofs_active_general", f"{base}/api/live-ofs-active-issues-ss?index=IS", NSE_OFS_REFERER),
        Endpoint("nse", "ofs_active_total_retail", f"{base}/api/live-ofs-active-issues-ss?index=totalForRetail", NSE_OFS_REFERER),
        Endpoint("nse", "ofs_forthcoming", f"{base}/api/live-ofs-forthcoming-issues", NSE_OFS_REFERER),
        Endpoint("nse", "ofs_past", f"{base}/api/live-ofs-past-issues", NSE_OFS_REFERER),
        Endpoint("nse", "ofs_past_general", f"{base}/api/live-ofs-past-issues?index=GENERAL", NSE_OFS_REFERER),
        Endpoint("nse", "ofs_past_retail", f"{base}/api/live-ofs-past-issues?index=RETAIL", NSE_OFS_REFERER),
        Endpoint("nse", "ofs_active_grouped", f"{base}/api/live-ofs-active-issues", NSE_OFS_REFERER),
        Endpoint("nse", "tender_active", f"{base}/api/liveTenderActive-issues", NSE_TENDER_REFERER),
        Endpoint("nse", "tender_forthcoming", f"{base}/api/liveTenderForthcoming-issues", NSE_TENDER_REFERER),
        Endpoint("nse", "tender_past", f"{base}/api/liveTenderPast-issues", NSE_TENDER_REFERER),
        Endpoint("nse", "rights_forthcoming", f"{base}/api/all-upcoming-issues?category=forthcomingIssues", NSE_REFERER),
        Endpoint("nse", "rights_active", f"{base}/api/liveWatchRights-issues?index=activeIssues", NSE_REFERER),
        Endpoint("nse", "rights_past", f"{base}/api/liveWatchRights-issues?index=pastIssues", NSE_REFERER),
        Endpoint("nse", "ipp_forthcoming", f"{base}/api/all-upcoming-issues?category=ipp", NSE_REFERER),
        Endpoint("nse", "ipp_active", f"{base}/api/liveIppActive-issues", NSE_REFERER),
        Endpoint("nse", "ipp_past", f"{base}/api/liveIppPast-issues", NSE_REFERER),
        Endpoint("nse", "invits_current", f"{base}/api/invits-current-issues", NSE_REFERER),
        Endpoint("nse", "invits_past", f"{base}/api/invits-past-issues", NSE_REFERER),
        Endpoint("nse", "reits_current", f"{base}/api/reits-current-issues", NSE_REFERER),
        Endpoint("nse", "reits_past", f"{base}/api/reits-past-issues", NSE_REFERER),
        # NOTE: G-Sec auction / non-competitive-bidding (ncb*, gsec, ncbgsec),
        # LWF, and Mutual Fund Service System (mfss) feeds were removed —
        # they are government-securities auctions and MF infrastructure, NOT
        # primary public issues. NCD / bond *public* issues are captured via
        # BSE's DPI flag (GetPublicIssue), NSE public-past-issues
        # securityType=N0/DEBT, and BSE bond_issue_documents.
        Endpoint("nse", "offer_documents_equity", f"{base}/api/corporates/offerdocs?index=equities", NSE_OFFER_DOCS_REFERER),
        Endpoint("nse", "offer_documents_sme", f"{base}/api/corporates/offerdocs?index=sme", NSE_OFFER_DOCS_REFERER),
        Endpoint("nse", "offer_documents_equity_companylist", f"{base}/api/corporates/offerdocs/equity/companylist", NSE_OFFER_DOCS_REFERER),
        Endpoint("nse", "offer_documents_sme_companylist", f"{base}/api/corporates/offerdocs/sme/companylist", NSE_OFFER_DOCS_REFERER),
        Endpoint("nse", "public_issue_advertisements", f"{base}/api/public-issue-advertisement?", NSE_PUBLIC_ADS_REFERER),
        Endpoint("nse", "public_issue_company_list", f"{base}/api/ipo-issue-company-list", NSE_PUBLIC_ADS_REFERER),
    ]


def bse_endpoints(as_of: date) -> list[Endpoint]:
    base = "https://api.bseindia.com/BseIndiaAPI/api"
    year_start = date(as_of.year, 1, 1).strftime("%Y%m%d")
    as_of_text = as_of.strftime("%Y%m%d")
    endpoints = [
        Endpoint("bse", "public_issue", f"{base}/GetPublicIssue/w", BSE_REFERER),
        Endpoint("bse", "public_issue_details", f"{base}/GetPublicIssue_par/w", BSE_REFERER),
        Endpoint("bse", "ipo_years", f"{base}/IPOYear/w", BSE_REFERER),
        Endpoint("bse", "ipo_tracker_current_year", f"{base}/IPOTrackerN/w?Fromdt={year_start}&Todt={as_of_text}", BSE_REFERER),
        Endpoint("bse", "ipo_documents", f"{base}/Pubissues_IPODRHP_par_ng/w", BSE_REFERER),
        Endpoint("bse", "ofs_date_list", f"{base}/Mkt_CurrDeri_dropDownDate_OFS_beta/w", BSE_REFERER),
        Endpoint("bse", "buyback_tender_documents", f"{base}/Mkt_Pubissues_FIS_BuybackTenderoffer_isd_ng/w", BSE_REFERER),
        Endpoint("bse", "buyback_open_market_documents", f"{base}/Pubissues_FIS_Buyback_Openmkt_isd_ng/w", BSE_REFERER),
        Endpoint("bse", "takeover_documents", f"{base}/Mkt_Pubissues_FIS_Takeover_isd_ng/w", BSE_REFERER),
        Endpoint("bse", "voluntary_delisting_documents", f"{base}/Pubissues_FIS_VoluntaryDelisting_isd_ng/w", BSE_REFERER),
        Endpoint("bse", "rights_issue_documents", f"{base}/Pubissues_FurtherIssuesummary_RI_isd_ng/w", BSE_REFERER),
        Endpoint("bse", "qip_documents", f"{base}/Pubissues_FurtherIssuesummary_QIP_isd_ng/w", BSE_REFERER),
        Endpoint("bse", "invit_placement_documents", f"{base}/Pubissues_get_InvitPlacement_ng/w", BSE_REFERER),
        Endpoint("bse", "invit_reit_documents", f"{base}/Pubissues_INVSTSandREITS_File_ng/w", BSE_REFERER),
        Endpoint("bse", "bond_issue_documents", f"{base}/Pubissues_BondIssues_DRHP_ng/w", BSE_REFERER),
        Endpoint("bse", "bond_issuance_years", f"{base}/Pubissues_Bond_Issuances_Fin_Year_ng/w", BSE_REFERER),
    ]
    for year in range(2017, as_of.year + 1):
        endpoints.append(Endpoint("bse", f"ipo_performance_mainboard_{year}", f"{base}/MoreCompanyN/w?Fromdt={year}&company=&flag=1&type=2", BSE_REFERER))
        endpoints.append(Endpoint("bse", f"ipo_performance_sme_{year}", f"{base}/MoreCompanyN/w?Fromdt={year}&company=&flag=2&type=2", BSE_REFERER))
    return endpoints


def selected_endpoints(source: str, as_of: date) -> list[Endpoint]:
    if source == "nse":
        return nse_endpoints()
    if source == "bse":
        return bse_endpoints(as_of)
    if source == "capitalmarket":
        return []
    if source == "prime":
        return []
    if source == "trendlyne":
        return []
    if source == "moneycontrol":
        return []
    if source == "all":
        return [*nse_endpoints(), *bse_endpoints(as_of)]
    raise ValueError(f"Unsupported source: {source}")


def fetch_endpoints(client: HttpClient, endpoints: Iterable[Endpoint]) -> list[tuple[Endpoint, HttpResult]]:
    results: list[tuple[Endpoint, HttpResult]] = []
    warmed_nse = False
    for endpoint in endpoints:
        if endpoint.source == "nse" and not warmed_nse:
            client.warm_nse()
            warmed_nse = True
        result = client.get(endpoint.url, referer=endpoint.referer, expect_json=endpoint.expect_json)
        results.append((endpoint, result))
    return results


def nested_endpoints_from_snapshots(snapshots: list[dict], as_of: date) -> list[Endpoint]:
    endpoints: list[Endpoint] = []
    endpoints.extend(nse_nested_endpoints(snapshots))
    endpoints.extend(bse_nested_endpoints(snapshots))
    return endpoints


def nse_nested_endpoints(snapshots: list[dict]) -> list[Endpoint]:
    base = "https://www.nseindia.com"
    endpoints: list[Endpoint] = []
    seen = set()
    current_symbols = nse_current_symbols(snapshots)
    public_issue_pans = nse_public_issue_pans(snapshots)
    for snapshot in snapshots:
        meta = snapshot.get("meta", {})
        if meta.get("source") != "nse" or meta.get("endpoint") != "ipo_current_issue":
            continue
        body = snapshot.get("body")
        if not isinstance(body, list):
            continue
        for row in body:
            if not isinstance(row, dict):
                continue
            symbol = row.get("symbol")
            series = row.get("series") or row.get("securityType") or "EQ"
            if not symbol:
                continue
            symbol_key = safe_key(symbol)
            series_key = safe_key(series)
            referer = f"{base}/market-data/issue-information?series={series}&symbol={symbol}&type=Active"
            candidates = [
                Endpoint("nse", f"issue_detail_{symbol_key}_{series_key}", f"{base}/api/ipo-detail?symbol={symbol}&series={series}", referer),
                Endpoint("nse", f"bid_details_{symbol_key}_{series_key}", f"{base}/api/ipo-bid-details?symbol={symbol}&series={series}", referer),
                Endpoint("nse", f"consolidated_bid_details_{symbol_key}", f"{base}/api/ipo-active-category?symbol={symbol}", referer),
                Endpoint("nse", f"demand_data_nse_{symbol_key}", f"{base}/api/ipo-chart-demand?symbol={symbol}&exchange=NSE", referer),
                Endpoint("nse", f"demand_data_all_{symbol_key}", f"{base}/api/ipo-chart-demand?symbol={symbol}&exchange=ALL", referer),
            ]
            for endpoint in candidates:
                if endpoint.name not in seen:
                    endpoints.append(endpoint)
                    seen.add(endpoint.name)
    for snapshot in snapshots:
        meta = snapshot.get("meta", {})
        endpoint_name = meta.get("endpoint")
        if meta.get("source") != "nse" or endpoint_name not in {"offer_documents_equity", "offer_documents_sme"}:
            continue
        body = snapshot.get("body")
        if not isinstance(body, list):
            continue
        for row in body:
            if not isinstance(row, dict):
                continue
            pan_no = clean_identifier(row.get("pan_no"))
            symbol = clean_identifier(row.get("symbol"))
            if not pan_no:
                continue
            if symbol not in current_symbols and pan_no not in public_issue_pans:
                continue
            pan_key = safe_key(pan_no)
            candidates = [
                Endpoint("nse", f"offer_document_detail_{pan_key}", f"{base}/api/offer-documents?pan_no={pan_no}", NSE_OFFER_DOCS_REFERER),
            ]
            candidates.extend(
                Endpoint(
                    "nse",
                    f"offer_abridged_{safe_key(prospectus_type)}_{pan_key}",
                    f"{base}/api/offer-documents-abridged-prospectus?pan_no={pan_no}&type={prospectus_type}",
                    NSE_OFFER_DOCS_REFERER,
                )
                for prospectus_type in ABRIDGED_PROSPECTUS_TYPES
            )
            for endpoint in candidates:
                if endpoint.name not in seen:
                    endpoints.append(endpoint)
                    seen.add(endpoint.name)
    return endpoints


def nse_current_symbols(snapshots: list[dict]) -> set[str]:
    symbols = set()
    for snapshot in snapshots:
        meta = snapshot.get("meta", {})
        if meta.get("source") != "nse" or meta.get("endpoint") != "ipo_current_issue":
            continue
        body = snapshot.get("body")
        if not isinstance(body, list):
            continue
        for row in body:
            if isinstance(row, dict):
                symbol = clean_identifier(row.get("symbol"))
                if symbol:
                    symbols.add(symbol)
    return symbols


def nse_public_issue_pans(snapshots: list[dict]) -> set[str]:
    pans = set()
    for snapshot in snapshots:
        meta = snapshot.get("meta", {})
        if meta.get("source") != "nse" or meta.get("endpoint") != "public_issue_advertisements":
            continue
        body = snapshot.get("body")
        if not isinstance(body, list):
            continue
        for row in body:
            if isinstance(row, dict):
                pan_no = clean_identifier(row.get("panNo"))
                if pan_no:
                    pans.add(pan_no)
    return pans


def bse_nested_endpoints(snapshots: list[dict]) -> list[Endpoint]:
    api_base = "https://api.bseindia.com/BseIndiaAPI/api"
    graph_base = "https://www.bseindia.com/BseGraph/charts/BarChart_IPO"
    endpoints: list[Endpoint] = []
    seen = set()
    for snapshot in snapshots:
        meta = snapshot.get("meta", {})
        if meta.get("source") != "bse" or meta.get("endpoint") != "public_issue_details":
            continue
        body = snapshot.get("body")
        rows = body.get("Table") if isinstance(body, dict) else None
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            issue_type = str(row.get("IR_flag") or row.get("IR_FLAG_FULL") or "").upper()
            ipo_no = row.get("IPO_NO")
            scrip_code = row.get("Scrip_cd")
            # CMN rows are BSE call-money / composite public-issue rows. They
            # use the same per-issue detail and bid-book endpoints as IPO/FPO.
            if issue_type not in {"IPO", "FPO", "CMN"} or not ipo_no:
                continue
            ipo_key = safe_key(ipo_no)
            referer = f"https://www.bseindia.com/markets/publicIssues/DisplayIPO?id={ipo_no}"
            candidates = [
                Endpoint("bse", f"issue_detail_{ipo_key}", f"{api_base}/GetMkt_ISSUE_BBS_IPO/w?IPO_NO={ipo_no}", referer),
                Endpoint("bse", f"bid_details_{ipo_key}", f"{api_base}/Pubissues_GetBkbldgCatdem_ng/w?IPO_NO={ipo_no}", referer),
                Endpoint("bse", f"consolidated_bid_details_{ipo_key}", f"{api_base}/Pubissues_GetBkbldgCatdem_PAR_ng/w?IPO_NO={ipo_no}", referer),
                Endpoint("bse", f"consolidated_bid_details_new_{ipo_key}", f"{api_base}/Pubissues_GetBkbldgCatdem_PAR_bbnew_ng/w?IPO_NO={ipo_no}", referer),
            ]
            if scrip_code:
                candidates.extend(
                    [
                        Endpoint("bse", f"demand_schedule_{ipo_key}", f"{api_base}/Pubissues_BSEDemandSchedule_otb_ng/w?Scripcode={scrip_code}&IPO_NO={ipo_no}", referer),
                        Endpoint("bse", f"demand_graph_bse_{ipo_key}", f"{graph_base}?Scripcode={scrip_code}&ir_flag={issue_type}&CType=B", referer, expect_json=False),
                        Endpoint("bse", f"demand_graph_consolidated_{ipo_key}", f"{graph_base}?Scripcode={scrip_code}&ir_flag={issue_type}&CType=C", referer, expect_json=False),
                    ]
                )
            for endpoint in candidates:
                if endpoint.name not in seen:
                    endpoints.append(endpoint)
                    seen.add(endpoint.name)
    return endpoints


def safe_key(value: object) -> str:
    text = str(value).strip().lower()
    return "".join(ch if ch.isalnum() else "_" for ch in text).strip("_")


def clean_identifier(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"-", "--", "NA", "N/A", "null", "None"}:
        return None
    return text
