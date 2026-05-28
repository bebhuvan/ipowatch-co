"""DeepSeek-backed primary filing processor for V3 prospectus facts.

The processor turns one primary filing PDF into a citation-verified
``prospectus_facts.json`` document. It is deliberately conservative:
unverified citations are redacted, not published.
"""

from __future__ import annotations

import hashlib
import http.client
import io
import json
import re
import subprocess
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .deepseek import DEFAULT_MODEL as DEEPSEEK_DEFAULT_MODEL
from .deepseek import DeepSeekClient
from .openrouter import DEFAULT_MODEL as OPENROUTER_DEFAULT_MODEL
from .openrouter import OpenRouterClient


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PDF_CACHE = PROJECT_ROOT / "data" / "cache" / "primary_filings"
PDF_TEXT_CACHE = PROJECT_ROOT / "data" / "cache" / "pdf_text"
SITE_V3 = PROJECT_ROOT / "data" / "ipo_watch_v3"
DEFAULT_OUTPUT_ROOT = SITE_V3 / "issues"
SCHEMA_VERSION = "3.0.0"
PDF_TEXT_CACHE_VERSION = 2
MAX_SLICE_CHARS = 120_000
TEXT_INPUT_MODE = "text"
PDF_INPUT_MODE = "pdf"
PDF_URL_INPUT_MODE = "pdf-url"
PDF_BASE64_INPUT_MODE = "pdf-base64"
PDFTOTEXT_EXTRACTOR = "pdftotext"
LITEPARSE_EXTRACTOR = "liteparse"
REQUIRED_SECTIONS = {"business", "financials", "risks"}
QUALITY_MAX_FAIL_REDACTION_RATE = 0.35
QUALITY_MAX_FAIL_REPAIR_RATE = 0.20
QUALITY_MAX_REVIEW_REDACTION_RATE = 0.20
QUALITY_MAX_REVIEW_REPAIR_RATE = 0.10
QUALITY_MIN_VERIFIED_FACTS = 25


SYSTEM_PROMPT = """You are extracting facts from a primary Indian public-issue filing for an SEO-rich IPO/debt-issue page.

Hard rules:
1. Output strict json only.
2. Use only the supplied PDF text slice. Do not use outside knowledge.
3. Every scalar fact must be an object with exactly this citation shape:
   {"value": <value-or-null>, "raw_excerpt": "<exact contiguous quote from the slice or null>", "source_page": <1-indexed PDF page or null>, "source_section": "<document heading or null>", "confidence": "high|medium|low"}
4. If a value is not explicitly supported by the slice, return null.
5. raw_excerpt must be exact contiguous text from the slice. Do not paraphrase, join distant text, add ellipses, or repair grammar.
6. The slice contains page delimiters like --- PDF PAGE 95 ---. Use them to set source_page, but never include the delimiter in raw_excerpt.
7. For every non-null value, raw_excerpt and source_page are mandatory. If you cannot provide both, set value to null.
8. Prefer short raw_excerpt values from one page. Do not quote across page boundaries. Never use "..." inside raw_excerpt.
9. For table-derived values, quote one exact printed row or compact contiguous table fragment from one page; otherwise return null.
10. Money is integer paise, percent is integer basis points, dates are ISO 8601, ratios/multiples are decimal strings.
11. Prefer concise, high-signal facts that help a reader understand the company, issue terms, timetable, intermediaries, risks, industry, financials, valuation, and red flags.
12. For issue terms, distinguish facts from the filing from live exchange state. Do not infer live subscription, allotment status, GMP, or listing estimate from a DRHP/RHP unless the filing explicitly states it.
13. Be complete rather than sparse. Extract every material fact in the requested schema that is visible in the slice, even when it feels repetitive. The downstream verifier and article writer will filter later.
14. Use arrays generously for article drafting: capture notable products, segments, locations, customers, suppliers, capacities, utilization, KPIs, period comparisons, use-of-proceeds rows, reservation rows, dates, litigations, proceedings, dependencies, and red flags.
15. Preserve context in the value. A fact like "Rs 1,443 billion" is weak unless the value says what it measures and for which market/period.
16. For narrative facts, prefer one complete sentence or short table row. Avoid keyword-only values unless the filing itself is only a keyword/table cell.

Extraction checklist:
- Company identity: business description, incorporation/history, promoters/group, headquarters/registered office, subsidiaries, facilities, branches, employees.
- Products and revenue: product families, services, brands, revenue mix, export/domestic mix, customer industries, major customers, customer concentration.
- Operations: manufacturing process, capacity, capacity utilization, raw materials, suppliers, supply concentration, logistics, quality certifications, licenses, technology/IP.
- Strategy: expansion plans, capex, new facilities, geographic expansion, product expansion, acquisition plans, competitive strengths, dependencies.
- Industry: market size, growth rates, demand drivers, headwinds, regulatory backdrop, peer/competition context, import/export dynamics, commodity/input trends.
- Issue economics: fresh issue, OFS, total issue size, shares, face value, price band, lot size, minimum investment, reservation, market maker, listing exchanges/platform.
- Use of proceeds: each object, amount, timeline, funding requirement, means of finance, deployment already made, monitoring agency.
- Timetable: bid/open/close, anchor, basis/allotment, refund/unblocking, credit of shares, listing, any T+ timeline described in the filing.
- Financials: revenue, EBITDA, PAT, margins, EPS, NAV, RoNW, ROCE, net worth, assets, borrowings, cash flows, working capital, receivables, payables, inventory, contingent liabilities, auditor qualifications, notable year-on-year changes.
- Risks and red flags: customer/supplier concentration, related-party dependence, promoter/group dependence, litigation, regulatory actions, tax proceedings, defaults/dues, indebtedness, working capital stress, negative cash flow, capacity/quality issues, raw-material volatility, FX exposure, geography concentration, competition.
- Governance/intermediaries: directors, KMP, promoters, shareholding pre/post issue, lead managers, registrar, auditors, legal counsel, bankers, market makers, sponsor bank, monitoring agency.
"""


SECTION_SPECS: list[dict[str, Any]] = [
    {
        "name": "business",
        "keywords": ["BUSINESS OVERVIEW", "OUR BUSINESS", "SUMMARY OF BUSINESS"],
        "min_page": 30,
        "pages_after": 45,
        "schema": {
            "incorporation_and_history": [],
            "registered_office": None,
            "corporate_office": None,
            "company_description": None,
            "business_model": None,
            "products_services": [],
            "brands": [],
            "customers": [],
            "customer_concentration": [],
            "suppliers": [],
            "supplier_concentration": [],
            "geographies": [],
            "revenue_mix": [],
            "export_domestic_mix": [],
            "facilities": [],
            "capacity_and_utilization": [],
            "manufacturing_process": [],
            "raw_materials": [],
            "licenses_certifications": [],
            "employees": [],
            "technology_or_ip": [],
            "growth_strategy": [],
            "competitive_strengths": [],
            "key_dependencies": [],
        },
    },
    {
        "name": "history_and_structure",
        "keywords": [
            "HISTORY AND CERTAIN CORPORATE MATTERS",
            "HISTORY AND CORPORATE STRUCTURE",
            "OUR HISTORY",
            "CORPORATE STRUCTURE",
            "GROUP COMPANIES",
        ],
        "min_page": 30,
        "pages_after": 45,
        "schema": {
            "incorporation": None,
            "name_changes": [],
            "major_events": [],
            "subsidiaries": [],
            "group_companies": [],
            "holding_company": None,
            "promoter_group_entities": [],
            "material_agreements": [],
            "corporate_structure": [],
        },
    },
    {
        "name": "industry",
        "keywords": ["INDUSTRY OVERVIEW", "INDUSTRY", "MARKET OVERVIEW"],
        "min_page": 30,
        "pages_after": 55,
        "schema": {
            "industry_name": None,
            "industry_segments": [],
            "market_size": [],
            "growth_rates": [],
            "drivers": [],
            "headwinds": [],
            "competition": [],
            "peer_landscape": [],
            "macro_story": [],
            "regulatory_context": [],
            "commodity_or_input_trends": [],
            "demand_supply_dynamics": [],
            "export_import_trends": [],
        },
    },
    {
        "name": "financials",
        "keywords": ["SUMMARY OF OUR FINANCIAL INFORMATION", "FINANCIAL INFORMATION", "RESTATED", "SUMMARY FINANCIAL INFORMATION"],
        "min_page": 25,
        "pages_after": 90,
        "schema": {
            "periods": [],
            "revenue": [],
            "ebitda": [],
            "pat": [],
            "assets": [],
            "debt": [],
            "cash_flow": [],
            "ratios": [],
            "segment_financials": [],
            "balance_sheet": [],
            "profit_and_loss": [],
            "restated_financial_tables": [],
            "working_capital": [],
            "trade_receivables": [],
            "trade_payables": [],
            "inventory": [],
            "net_worth": [],
            "eps_nav_ronw": [],
            "roe_roce": [],
            "margins": [],
            "borrowings": [],
            "contingent_liabilities": [],
            "auditor_qualifications": [],
            "accounting_policies_or_changes": [],
            "notable_trends": [],
        },
    },
    {
        "name": "management_discussion",
        "keywords": [
            "MANAGEMENT'S DISCUSSION AND ANALYSIS",
            "MANAGEMENT DISCUSSION AND ANALYSIS",
            "RESULTS OF OPERATIONS",
            "FINANCIAL CONDITION AND RESULTS OF OPERATIONS",
        ],
        "min_page": 80,
        "pages_after": 70,
        "schema": {
            "business_overview": None,
            "significant_factors": [],
            "revenue_drivers": [],
            "expense_drivers": [],
            "period_comparisons": [],
            "margin_analysis": [],
            "liquidity_and_capital_resources": [],
            "cash_flow_discussion": [],
            "working_capital_discussion": [],
            "debt_discussion": [],
            "known_trends_or_uncertainties": [],
            "seasonality": [],
            "future_outlook_from_filing": [],
        },
    },
    {
        "name": "risks",
        "keywords": ["RISK FACTORS"],
        "min_page": 10,
        "pages_after": 95,
        "schema": {
            "risk_factor_count": None,
            "top_risks": [],
            "financial_risks": [],
            "regulatory_risks": [],
            "customer_supplier_risks": [],
            "promoter_legal_risks": [],
            "issue_specific_risks": [],
            "business_dependency_risks": [],
            "manufacturing_operational_risks": [],
            "working_capital_risks": [],
            "indebtedness_risks": [],
            "cash_flow_risks": [],
            "related_party_risks": [],
            "litigation_risks": [],
            "tax_risks": [],
            "raw_material_risks": [],
            "foreign_exchange_risks": [],
            "competition_risks": [],
            "geographic_concentration_risks": [],
            "red_flags": [],
        },
    },
    {
        "name": "offer",
        "keywords": ["OBJECTS OF THE OFFER", "OBJECTS OF THE ISSUE", "OBJECTS OF THE NET ISSUE", "THE ISSUE", "THE OFFER"],
        "min_page": 20,
        "pages_after": 55,
        "schema": {
            "issue_type": None,
            "issue_structure": None,
            "total_issue_size": None,
            "number_of_shares": None,
            "face_value": None,
            "fresh_issue": None,
            "offer_for_sale": None,
            "net_issue": None,
            "market_maker_portion": None,
            "use_of_proceeds": [],
            "objects_of_issue": [],
            "funding_requirements": [],
            "means_of_finance": [],
            "deployment_schedule": [],
            "amount_deployed_before_issue": [],
            "monitoring_agency": None,
            "interim_use_of_funds": None,
            "selling_shareholders": [],
            "lead_managers": [],
            "registrar": None,
            "debt_terms": [],
        },
    },
    {
        "name": "issue_terms",
        "keywords": [
            "ISSUE STRUCTURE",
            "TERMS OF THE ISSUE",
            "ISSUE PROGRAMME",
            "ISSUE SCHEDULE",
            "BASIS OF ALLOTMENT",
            "ISSUE PROCEDURE",
            "OFFER PROGRAMME",
        ],
        "min_page": 20,
        "pages_after": 55,
        "schema": {
            "exchange_platform": None,
            "listing_exchanges": [],
            "book_building_or_fixed_price": None,
            "price_band_lower": None,
            "price_band_upper": None,
            "issue_price": None,
            "face_value": None,
            "lot_size": None,
            "minimum_application_shares": None,
            "minimum_application_amount": None,
            "maximum_retail_application": None,
            "category_reservation": [],
            "qib_nii_retail_split": [],
            "anchor_investor_portion": None,
            "market_maker_reservation": None,
            "employee_shareholder_reservation": [],
            "bid_lot_table": [],
            "application_amounts": [],
            "issue_timetable": [],
            "basis_of_allotment": None,
            "refund_or_unblocking": None,
            "credit_of_shares": None,
            "listing_date": None,
            "trading_lot": None,
            "ranking_or_security_terms": [],
        },
    },
    {
        "name": "intermediaries",
        "keywords": [
            "GENERAL INFORMATION",
            "BOOK RUNNING LEAD MANAGER",
            "BOOK RUNNING LEAD MANAGERS",
            "LEAD MANAGER",
            "REGISTRAR TO THE ISSUE",
            "ISSUE RELATED INFORMATION",
        ],
        "min_page": 3,
        "pages_after": 45,
        "schema": {
            "lead_managers": [],
            "registrar": None,
            "legal_counsel": [],
            "statutory_auditors": [],
            "peer_review_auditor": None,
            "bankers_to_the_issue": [],
            "refund_bank": None,
            "syndicate_members": [],
            "market_makers": [],
            "sponsor_bank": None,
            "monitoring_agency": None,
            "listing_exchanges": [],
            "ipo_grading_agency": None,
        },
    },
    {
        "name": "valuation_and_peers",
        "keywords": ["BASIS FOR OFFER PRICE", "BASIS FOR THE OFFER PRICE", "COMPARISON WITH INDUSTRY PEERS", "PEER GROUP"],
        "min_page": 40,
        "pages_after": 30,
        "schema": {
            "valuation_metrics": [],
            "peer_comparison": [],
            "eps_nav_ronw": [],
            "price_to_earnings": [],
            "net_asset_value": [],
            "return_metrics": [],
            "industry_peer_table": [],
            "pricing_rationale": [],
        },
    },
    {
        "name": "governance",
        "keywords": ["OUR PROMOTERS", "OUR MANAGEMENT", "OUTSTANDING LITIGATION", "CAPITAL STRUCTURE"],
        "min_page": 50,
        "pages_after": 70,
        "schema": {
            "promoters": [],
            "directors": [],
            "kmp": [],
            "shareholding": [],
            "promoter_shareholding": [],
            "public_shareholding": [],
            "selling_shareholders": [],
            "litigation": [],
            "criminal_proceedings": [],
            "tax_proceedings": [],
            "regulatory_actions": [],
            "defaults_or_dues": [],
            "contingent_liabilities": [],
            "promoter_legal_flags": [],
            "related_party_transactions": [],
            "capital_structure": [],
            "lock_in_details": [],
            "employee_stock_options": [],
            "dividend_policy": None,
        },
    },
]


@dataclass(frozen=True)
class PDFText:
    text: str
    pages: list[str]
    extractor: str = PDFTOTEXT_EXTRACTOR


@dataclass(frozen=True)
class FilingJob:
    slug: str
    url: str
    document_type: str
    source: str | None = None


def discover_jobs(site_root: Path = SITE_V3, *, limit: int | None = None, include_existing: bool = False) -> list[FilingJob]:
    jobs: list[FilingJob] = []
    for path in sorted((site_root / "issues" / "by-slug").glob("*.json")):
        doc = _read_json(path)
        docs = doc.get("documents") or {}
        for field, dtype in (("rhp_url", "RHP"), ("drhp_url", "DRHP"), ("prospectus_url", "prospectus")):
            url = docs.get(field)
            if isinstance(url, str) and url:
                if not include_existing and _has_current_extraction(site_root, doc["slug"], url):
                    break
                jobs.append(FilingJob(slug=doc["slug"], url=url, document_type=dtype, source="site_v3"))
                break
        if limit is not None and len(jobs) >= limit:
            break
    return jobs


def _has_current_extraction(site_root: Path, slug: str, url: str) -> bool:
    path = site_root / "issues" / slug / "prospectus_facts.json"
    if not path.exists():
        return False
    try:
        doc = _read_json(path)
    except (OSError, json.JSONDecodeError):
        return False
    if doc.get("document_url") != url:
        return False
    quality_state = (doc.get("quality") or {}).get("state")
    return bool((doc.get("deepseek") or {}).get("used") and quality_state in {"pass", "review"})


def process_filing(
    job: FilingJob,
    *,
    model: str | None = None,
    provider: str = "deepseek",
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    dry_run: bool = False,
    client: Any | None = None,
    input_mode: str = TEXT_INPUT_MODE,
    pdf_engine: str | None = "native",
    text_extractor: str = PDFTOTEXT_EXTRACTOR,
) -> dict[str, Any]:
    pdf_bytes, pdf_path = download_pdf(job.url)
    pdf = extract_pdf_text(pdf_path, backend=text_extractor)
    sections: dict[str, Any] = {}
    calls: list[dict[str, Any]] = []
    total_cost = 0.0

    if dry_run:
        return _document(job, pdf_bytes, pdf_path, pdf, {}, [], "dry_run", 0.0)

    model = model or (OPENROUTER_DEFAULT_MODEL if provider == "openrouter" else DEEPSEEK_DEFAULT_MODEL)
    client = client or _client_for_provider(provider)
    for spec in SECTION_SPECS:
        slice_text, page_start, page_end, method = slice_for_spec(pdf, spec)
        if method == "not_found" or not slice_text.strip():
            sections[spec["name"]] = {spec["name"]: spec["schema"]}
            calls.append(
                {
                    "section": spec["name"],
                    "cache_key": None,
                    "cached": False,
                    "model": None,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "estimated_cost_usd": 0.0,
                    "page_range": [page_start, page_end],
                    "slice_method": method,
                    "input_mode": input_mode,
                    "text_extractor": text_extractor,
                    "skipped": True,
                    "skip_reason": "section_not_found",
                }
            )
            continue
        if input_mode == TEXT_INPUT_MODE:
            response = client.chat(
                user=_user_prompt(spec, slice_text, page_start, page_end),
                system=SYSTEM_PROMPT,
                model=model,
                temperature=0.0,
                response_format="json_object",
                max_tokens=24_000,
                purpose=f"v3_filing:{job.slug}:{spec['name']}",
                extra_telemetry={
                    "slug": job.slug,
                    "document_type": job.document_type,
                    "section": spec["name"],
                    "page_start": page_start,
                    "page_end": page_end,
                    "slice_method": method,
                    "input_mode": input_mode,
                },
            )
        else:
            response = _chat_with_pdf_section(
                client,
                job=job,
                pdf_path=pdf_path,
                spec=spec,
                model=model,
                page_start=page_start,
                page_end=page_end,
                method=method,
                input_mode=input_mode,
                pdf_engine=pdf_engine,
                max_tokens=24_000,
                purpose=f"v3_filing:{job.slug}:{spec['name']}",
            )
        sections[spec["name"]] = response.json_content
        total_cost += response.estimated_cost_usd
        calls.append(
            {
                "section": spec["name"],
                "cache_key": response.cache_key,
                "cached": response.cached,
                "model": response.model,
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
                "estimated_cost_usd": round(response.estimated_cost_usd, 6),
                "page_range": [page_start, page_end],
                "slice_method": method,
                "input_mode": input_mode,
                "text_extractor": text_extractor,
            }
        )

    doc = _document(job, pdf_bytes, pdf_path, pdf, sections, calls, "extracted", total_cost)
    doc["shape_validation"] = redact_unshaped_facts(doc)
    citation_report = validate_and_redact_citations(doc, pdf)
    doc["citation_validation"] = citation_report
    doc["quality"] = assess_extraction_quality(doc)
    doc["extraction_status"] = _extraction_status(doc["quality"], citation_report)
    out_path = output_root / job.slug / "prospectus_facts.json"
    _write_json(out_path, doc)
    return doc


def benchmark_models(
    job: FilingJob,
    *,
    models: list[str],
    provider: str = "openrouter",
    section_name: str = "business",
    max_chars: int = 30_000,
    input_mode: str = TEXT_INPUT_MODE,
    pdf_engine: str | None = "native",
    text_extractor: str = PDFTOTEXT_EXTRACTOR,
) -> dict[str, Any]:
    """Run one filing section through several models and score extraction quality."""
    pdf_bytes, pdf_path = download_pdf(job.url)
    pdf = extract_pdf_text(pdf_path, backend=text_extractor)
    spec = next((item for item in SECTION_SPECS if item["name"] == section_name), SECTION_SPECS[0])
    slice_text, page_start, page_end, method = slice_for_spec(pdf, spec)
    if len(slice_text) > max_chars:
        slice_text = slice_text[:max_chars]

    results: list[dict[str, Any]] = []
    for model in models:
        started = time.monotonic()
        row: dict[str, Any] = {"model": model, "provider": provider}
        try:
            client = _client_for_provider(provider)
            if input_mode == TEXT_INPUT_MODE:
                response = client.chat(
                    user=_user_prompt(spec, slice_text, page_start, page_end),
                    system=SYSTEM_PROMPT,
                    model=model,
                    temperature=0.0,
                    response_format="json_object",
                    max_tokens=8_000,
                    purpose=f"v3_filing_benchmark:{job.slug}:{section_name}:{model}",
                    extra_telemetry={
                        "slug": job.slug,
                        "benchmark": True,
                        "section": section_name,
                        "page_start": page_start,
                        "page_end": page_end,
                        "slice_chars": len(slice_text),
                        "input_mode": input_mode,
                        "text_extractor": text_extractor,
                    },
                )
            else:
                response = _chat_with_pdf_section(
                    client,
                    job=job,
                    pdf_path=pdf_path,
                    spec=spec,
                    model=model,
                    page_start=page_start,
                    page_end=page_end,
                    method=method,
                    input_mode=input_mode,
                    pdf_engine=pdf_engine,
                    max_tokens=8_000,
                    purpose=f"v3_filing_benchmark:{job.slug}:{section_name}:{model}",
                    extra_telemetry={"benchmark": True, "slice_chars": len(slice_text)},
                )
            doc = _document(
                job,
                pdf_bytes,
                pdf_path,
                pdf,
                {section_name: response.json_content},
                [],
                "benchmark",
                response.estimated_cost_usd,
            )
            citation = validate_and_redact_citations(doc, pdf)
            row.update(
                {
                    "ok": True,
                    "json_valid": response.json_content is not None,
                    "latency_ms": int((time.monotonic() - started) * 1000),
                    "prompt_tokens": response.prompt_tokens,
                    "completion_tokens": response.completion_tokens,
                    "total_tokens": response.total_tokens,
                    "estimated_cost_usd": round(response.estimated_cost_usd, 6),
                    "cached": response.cached,
                    "citation_checked": citation["checked_count"],
                    "citation_repaired": citation["repaired_count"],
                    "citation_redacted": citation["redacted_count"],
                    "citation_pass_rate": _pass_rate(citation),
                    "top_level_keys": sorted((response.json_content or {}).keys())[:10]
                    if isinstance(response.json_content, dict)
                    else [],
                }
            )
        except Exception as exc:  # noqa: BLE001 - benchmark reports per-model failures
            row.update(
                {
                    "ok": False,
                    "json_valid": False,
                    "latency_ms": int((time.monotonic() - started) * 1000),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        results.append(row)

    return {
        "slug": job.slug,
        "document_type": job.document_type,
        "url": job.url,
        "section": section_name,
        "slice_chars": len(slice_text),
        "page_range": [page_start, page_end],
        "slice_method": method,
        "input_mode": input_mode,
        "pdf_engine": pdf_engine if input_mode != TEXT_INPUT_MODE else None,
        "text_extractor": text_extractor,
        "models": results,
    }


def download_pdf(url: str) -> tuple[bytes, Path]:
    PDF_CACHE.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    path = PDF_CACHE / f"{key[:24]}.pdf"
    if path.exists():
        return path.read_bytes(), path
    data = _download_with_retries(url)
    data = _unwrap_pdf(data, url)
    path.write_bytes(data)
    return data, path


def _client_for_provider(provider: str) -> Any:
    if provider == "deepseek":
        return DeepSeekClient()
    if provider == "openrouter":
        return OpenRouterClient()
    raise ValueError(f"unsupported filing extraction provider: {provider}")


def _chat_with_pdf_section(
    client: Any,
    *,
    job: FilingJob,
    pdf_path: Path,
    spec: dict[str, Any],
    model: str,
    page_start: int,
    page_end: int,
    method: str,
    input_mode: str,
    pdf_engine: str | None,
    max_tokens: int,
    purpose: str,
    extra_telemetry: dict[str, Any] | None = None,
) -> Any:
    if not hasattr(client, "chat_with_pdf"):
        raise ValueError("PDF input mode is currently implemented for the OpenRouter provider")
    pdf_url = job.url if input_mode in {PDF_INPUT_MODE, PDF_URL_INPUT_MODE} and _looks_like_pdf_url(job.url) else None
    if input_mode == PDF_URL_INPUT_MODE and not pdf_url:
        raise ValueError(f"input_mode={PDF_URL_INPUT_MODE} requires a direct public PDF URL, got: {job.url}")
    return client.chat_with_pdf(
        prompt=_pdf_user_prompt(spec, page_start, page_end),
        pdf_url=pdf_url,
        pdf_path=None if pdf_url else pdf_path,
        pdf_filename=f"{job.slug}.pdf",
        system=SYSTEM_PROMPT,
        model=model,
        temperature=0.0,
        response_format="json_object",
        max_tokens=max_tokens,
        purpose=purpose,
        pdf_engine=pdf_engine,  # type: ignore[arg-type]
        extra_telemetry={
            "slug": job.slug,
            "document_type": job.document_type,
            "section": spec["name"],
            "page_start": page_start,
            "page_end": page_end,
            "slice_method": method,
            "input_mode": input_mode,
            **(extra_telemetry or {}),
        },
    )


def _pass_rate(report: dict[str, Any]) -> float | None:
    checked = report.get("checked_count") or 0
    if not checked:
        return None
    return round((checked - report.get("redacted_count", 0)) / checked, 4)


def _download_with_retries(url: str, attempts: int = 4) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 IPO-Watch/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=180) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError, http.client.IncompleteRead) as exc:
            last_error = exc
            if attempt == attempts:
                break
            time.sleep(min(2 ** attempt, 10))
    raise RuntimeError(f"failed to download filing after {attempts} attempts: {url}: {last_error}")


def extract_pdf_text(pdf_path: Path, *, backend: str = PDFTOTEXT_EXTRACTOR, use_cache: bool = True) -> PDFText:
    if backend not in {PDFTOTEXT_EXTRACTOR, LITEPARSE_EXTRACTOR}:
        raise ValueError(f"unsupported PDF text extractor: {backend}")
    cache_path = _pdf_text_cache_path(pdf_path, backend)
    if use_cache and cache_path.exists():
        cached = _read_json(cache_path)
        if cached.get("cache_version") == PDF_TEXT_CACHE_VERSION:
            return PDFText(text=cached["text"], pages=list(cached["pages"]), extractor=str(cached.get("extractor") or backend))

    if backend == PDFTOTEXT_EXTRACTOR:
        pdf = _extract_pdf_text_pdftotext(pdf_path)
    else:
        pdf = _extract_pdf_text_liteparse(pdf_path)

    if use_cache:
        _write_json(
            cache_path,
            {
                "cache_version": PDF_TEXT_CACHE_VERSION,
                "extractor": pdf.extractor,
                "pages": pdf.pages,
                "text": pdf.text,
            },
        )
    return pdf


def _extract_pdf_text_pdftotext(pdf_path: Path) -> PDFText:
    raw = subprocess.check_output(["pdftotext", "-layout", str(pdf_path), "-"])
    text = raw.decode("utf-8", errors="replace")
    return _pdf_text_from_pages(text.split("\f"), PDFTOTEXT_EXTRACTOR)


def _extract_pdf_text_liteparse(pdf_path: Path) -> PDFText:
    try:
        from liteparse import LiteParse
    except ImportError as exc:
        raise RuntimeError(
            "LiteParse is not installed. Run `.venv/bin/python -m pip install -r requirements-extractors.txt`."
        ) from exc

    result = LiteParse().parse(
        pdf_path,
        ocr_enabled=False,
        precise_bounding_box=False,
        timeout=900,
    )
    pages = _liteparse_pages(result.pages)
    if not pages and getattr(result, "text", None):
        pages = str(result.text).split("\f")
    return _pdf_text_from_pages(pages, LITEPARSE_EXTRACTOR)


def _liteparse_pages(parsed_pages: list[Any]) -> list[str]:
    numbered: list[tuple[int, str]] = []
    for fallback_idx, page in enumerate(parsed_pages, start=1):
        page_num = getattr(page, "pageNum", None)
        if not isinstance(page_num, int) or page_num < 1:
            page_num = fallback_idx
        numbered.append((page_num, str(getattr(page, "text", "") or "")))
    if not numbered:
        return []
    pages = [""] * max(page_num for page_num, _ in numbered)
    for page_num, text in numbered:
        pages[page_num - 1] = text
    return pages


def _pdf_text_from_pages(pages: list[str], extractor: str) -> PDFText:
    cleaned = [str(page or "") for page in pages]
    return PDFText(text="\f".join(cleaned), pages=cleaned, extractor=extractor)


def _pdf_text_cache_path(pdf_path: Path, backend: str) -> Path:
    digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    return PDF_TEXT_CACHE / backend / f"{digest[:24]}.json"


def slice_for_spec(pdf: PDFText, spec: dict[str, Any]) -> tuple[str, int, int, str]:
    start_page = None
    min_page = int(spec.get("min_page") or 1)
    for keyword in spec["keywords"]:
        found = _find_keyword_page(pdf, keyword, min_page=min_page)
        if found is not None:
            start_page = found
            break
    if start_page is None:
        return "", 1, 1, "not_found"
    else:
        method = "keyword"
    end_page = min(len(pdf.pages), start_page + int(spec.get("pages_after", 40)))
    text = _slice_with_page_markers(pdf, start_page, end_page)
    if len(text) > MAX_SLICE_CHARS:
        text = text[:MAX_SLICE_CHARS]
    return text, start_page, end_page, method


def _slice_with_page_markers(pdf: PDFText, start_page: int, end_page: int) -> str:
    chunks = []
    for page_num in range(start_page, end_page + 1):
        page = pdf.pages[page_num - 1] if page_num - 1 < len(pdf.pages) else ""
        chunks.append(f"\n\n--- PDF PAGE {page_num} ---\n\n{page}")
    return "\n".join(chunks)


def validate_and_redact_citations(document: dict[str, Any], pdf: PDFText) -> dict[str, Any]:
    checked = repaired = redacted = 0
    failures = []
    for path, leaf in _iter_fact_leaves(document.get("facts") or {}):
        if not isinstance(leaf, dict) or "value" not in leaf:
            continue
        if leaf.get("value") is None:
            continue
        checked += 1
        excerpt = leaf.get("raw_excerpt")
        page = leaf.get("source_page")
        if isinstance(excerpt, str) and isinstance(page, int) and _excerpt_on_page(pdf, excerpt, page):
            continue
        found = _find_excerpt_page(pdf, excerpt) if isinstance(excerpt, str) else None
        if found is not None:
            leaf["source_page"] = found
            repaired += 1
            continue
        _redact(leaf)
        redacted += 1
        failures.append({"path": path, "old_page": page, "raw_excerpt": str(excerpt or "")[:160]})
    return {
        "checked_count": checked,
        "repaired_count": repaired,
        "redacted_count": redacted,
        "state": "clean_with_redactions" if redacted else "clean",
        "failures": failures[:200],
    }


def redact_unshaped_facts(document: dict[str, Any]) -> dict[str, Any]:
    """Remove primitive fact leaves that do not carry citation metadata."""
    redacted: list[dict[str, Any]] = []
    facts = document.get("facts")
    if isinstance(facts, dict):
        _redact_unshaped_in_place(facts, "", redacted)
    return {"redacted_count": len(redacted), "redactions": redacted[:200]}


def _redact_unshaped_in_place(value: Any, path: str, redacted: list[dict[str, Any]]) -> Any:
    if isinstance(value, dict):
        if "value" in value:
            return value
        for key, child in list(value.items()):
            child_path = f"{path}.{key}" if path else str(key)
            value[key] = _redact_unshaped_in_place(child, child_path, redacted)
        return value
    if isinstance(value, list):
        for idx, child in enumerate(list(value)):
            child_path = f"{path}[{idx}]"
            value[idx] = _redact_unshaped_in_place(child, child_path, redacted)
        return value
    if value is None:
        return None
    redacted.append({"path": path, "value_preview": str(value)[:120]})
    return None


def assess_extraction_quality(document: dict[str, Any]) -> dict[str, Any]:
    """Score extraction quality for batch gating and public publish decisions."""
    calls = document.get("deepseek", {}).get("calls") or []
    missing_sections = sorted(
        str(call.get("section"))
        for call in calls
        if call.get("slice_method") == "not_found" or call.get("skipped")
    )
    missing_required = [section for section in missing_sections if section in REQUIRED_SECTIONS]
    citation = document.get("citation_validation") or {}
    checked = int(citation.get("checked_count") or 0)
    redacted = int(citation.get("redacted_count") or 0)
    repaired = int(citation.get("repaired_count") or 0)
    redaction_rate = round(redacted / checked, 4) if checked else None
    repair_rate = round(repaired / checked, 4) if checked else None
    leaves = list(_iter_fact_leaves(document.get("facts") or {}))
    verified_facts = sum(1 for _, leaf in leaves if isinstance(leaf, dict) and leaf.get("value") is not None)

    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if missing_required:
        failures.append({"code": "missing_required_sections", "sections": missing_required})
    if checked < QUALITY_MIN_VERIFIED_FACTS:
        failures.append({"code": "too_few_checked_facts", "checked_count": checked, "minimum": QUALITY_MIN_VERIFIED_FACTS})
    if redaction_rate is not None and redaction_rate > QUALITY_MAX_FAIL_REDACTION_RATE:
        failures.append({"code": "redaction_rate_too_high", "rate": redaction_rate, "maximum": QUALITY_MAX_FAIL_REDACTION_RATE})
    elif redaction_rate is not None and redaction_rate > QUALITY_MAX_REVIEW_REDACTION_RATE:
        warnings.append({"code": "redaction_rate_review", "rate": redaction_rate, "review_threshold": QUALITY_MAX_REVIEW_REDACTION_RATE})
    if repair_rate is not None and repair_rate > QUALITY_MAX_FAIL_REPAIR_RATE:
        failures.append({"code": "repair_rate_too_high", "rate": repair_rate, "maximum": QUALITY_MAX_FAIL_REPAIR_RATE})
    elif repair_rate is not None and repair_rate > QUALITY_MAX_REVIEW_REPAIR_RATE:
        warnings.append({"code": "repair_rate_review", "rate": repair_rate, "review_threshold": QUALITY_MAX_REVIEW_REPAIR_RATE})
    optional_missing = [section for section in missing_sections if section not in REQUIRED_SECTIONS]
    if optional_missing:
        warnings.append({"code": "missing_optional_sections", "sections": optional_missing})

    state = "fail" if failures else "review" if warnings else "pass"
    return {
        "state": state,
        "checked_count": checked,
        "verified_fact_count": verified_facts,
        "redacted_count": redacted,
        "repaired_count": repaired,
        "redaction_rate": redaction_rate,
        "repair_rate": repair_rate,
        "missing_sections": missing_sections,
        "missing_required_sections": missing_required,
        "warnings": warnings,
        "failures": failures,
    }


def _extraction_status(quality: dict[str, Any], citation_report: dict[str, Any]) -> str:
    if quality.get("state") == "fail":
        return "quality_failed"
    if quality.get("state") == "review":
        return "review"
    return "clean_with_redactions" if citation_report.get("redacted_count") else "clean"


def _document(
    job: FilingJob,
    pdf_bytes: bytes,
    pdf_path: Path,
    pdf: PDFText,
    facts: dict[str, Any],
    calls: list[dict[str, Any]],
    status: str,
    total_cost: float,
) -> dict[str, Any]:
    return {
        "$schema": "https://ipo-watch.local/schema/v3/prospectus-facts.schema.json",
        "schema_version": SCHEMA_VERSION,
        "slug": job.slug,
        "document_type": job.document_type,
        "document_url": job.url,
        "document_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
        "local_pdf_path": str(pdf_path),
        "pdf_pages": len(pdf.pages),
        "pdf_text_chars": len(pdf.text),
        "pdf_text_extractor": pdf.extractor,
        "extraction_status": status,
        "extracted_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "facts": facts,
        "redactions": [],
        "deepseek": {
            "used": bool(calls),
            "model": calls[0]["model"] if calls else None,
            "calls": calls,
            "cost_usd": f"{total_cost:.6f}",
            "tokens_in": sum(c.get("prompt_tokens", 0) for c in calls),
            "tokens_out": sum(c.get("completion_tokens", 0) for c in calls),
        },
        "citation_validation": {"state": "not_checked", "checked_count": 0, "repaired_count": 0, "redacted_count": 0},
    }


def _user_prompt(spec: dict[str, Any], text: str, page_start: int, page_end: int) -> str:
    return (
        "Return json with this top-level shape:\n"
        f'{{"{spec["name"]}": {json.dumps(spec["schema"], ensure_ascii=False)}}}\n\n'
        f"PDF page range: {page_start}-{page_end}. Use exact 1-indexed source_page values.\n\n"
        f"TEXT SLICE:\n{text}"
    )


def _pdf_user_prompt(spec: dict[str, Any], page_start: int, page_end: int) -> str:
    return (
        "Return json with this top-level shape:\n"
        f'{{"{spec["name"]}": {json.dumps(spec["schema"], ensure_ascii=False)}}}\n\n'
        f"Use only the attached PDF. Focus on PDF pages {page_start}-{page_end}, "
        "but you may inspect nearby pages if the section boundary is imperfect. "
        "Use exact 1-indexed PDF source_page values from the attached PDF. "
        "Every raw_excerpt must be exact contiguous text from the PDF, because it "
        "will be independently verified before publication."
    )


def _looks_like_pdf_url(url: str) -> bool:
    return bool(re.search(r"\.pdf(?:$|[?#])", url, flags=re.IGNORECASE))


def _unwrap_pdf(data: bytes, url: str) -> bytes:
    if data[:4] != b"PK\x03\x04":
        return data
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        pdfs = [name for name in zf.namelist() if name.lower().endswith(".pdf")]
        if not pdfs:
            raise ValueError(f"ZIP contains no PDF: {url}")
        return zf.read(max(pdfs, key=lambda name: zf.getinfo(name).file_size))


def _find_keyword_page(pdf: PDFText, keyword: str, *, min_page: int = 1) -> int | None:
    needle = keyword.casefold()
    for idx, page in enumerate(pdf.pages, start=1):
        if idx < max(3, min_page):
            continue
        if not _looks_like_section_heading_page(page, keyword):
            continue
        if needle in page.casefold():
            return idx
    return None


def _looks_like_section_heading_page(page: str, keyword: str) -> bool:
    upper = page.upper()
    head = upper[:2500]
    needle = keyword.upper()
    if "TABLE OF CONTENTS" in head:
        return False
    heading_like = False
    for raw_line in head.splitlines()[:30]:
        line = " ".join(raw_line.split())
        if "BEGINNING ON PAGE" in line:
            continue
        if _heading_line_matches(line, needle):
            heading_like = True
            break
    if not heading_like:
        return False
    return True


def _heading_line_matches(line: str, needle: str) -> bool:
    if needle not in line or len(line) > len(needle) + 80:
        return False
    if line == needle or line.startswith(needle):
        return True
    if line.startswith("SECTION") and needle in line:
        return True
    if re.match(r"^\d+\.?\s+", line) and needle in line and any(
        marker in needle for marker in ("COMPARISON", "PEER", "BASIS")
    ):
        return True
    return False


def _iter_fact_leaves(obj: Any, path: str = ""):
    if isinstance(obj, dict):
        if "value" in obj and {"raw_excerpt", "source_page", "source_section", "confidence"}.intersection(obj):
            yield path or "$", obj
            return
        for key, value in obj.items():
            child = f"{path}.{key}" if path else str(key)
            yield from _iter_fact_leaves(value, child)
    elif isinstance(obj, list):
        for idx, value in enumerate(obj):
            yield from _iter_fact_leaves(value, f"{path}[{idx}]")


def _excerpt_on_page(pdf: PDFText, excerpt: str, page: int) -> bool:
    if page < 1 or page > len(pdf.pages):
        return False
    return _norm(excerpt) in _norm(pdf.pages[page - 1])


def _find_excerpt_page(pdf: PDFText, excerpt: str) -> int | None:
    needle = _norm(excerpt)
    if not needle:
        return None
    for idx, page in enumerate(pdf.pages, start=1):
        if needle in _norm(page):
            return idx
    return None


def _redact(leaf: dict[str, Any]) -> None:
    leaf["value"] = None
    leaf["raw_excerpt"] = None
    leaf["source_page"] = None
    leaf["source_section"] = None
    leaf["confidence"] = "low"


def _norm(text: str) -> str:
    return " ".join(str(text).replace("\u00a0", " ").split()).casefold()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
