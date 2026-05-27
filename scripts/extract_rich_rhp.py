"""Rich RHP extraction targeting docs/data/IPO_PAGE_SCHEMA.md.

This is the schema-driven multi-pass extractor for the ipowatch.co page.
The page schema is the contract; this script produces JSON that matches.

Pipeline
--------
1. Download the RHP PDF (cached by URL hash under data/cache/rhp_pdfs/).
2. Extract text with ``pdftotext`` (preserves form-feed page breaks).
3. Build a ``page_index`` mapping char offset → page number.
4. For each section of the page schema, slice the text by char range,
   compute the page range covered by the slice, and call DeepSeek with
   a section-targeted prompt. Each call asks the model to emit
   ``{value, raw_excerpt, source_page, source_section, confidence}``
   for every extracted leaf.
5. Final synthesis pass merges section outputs and resolves conflicts.
6. Output goes to ``data/site_v2/issues/<slug>/prospectus.json``.

Usage::

    python scripts/extract_rich_rhp.py --url <RHP_URL> --slug vasa-denticity

Cost note: 7 section calls + 1 synthesis ≈ 8 DeepSeek calls per RHP.
Each ~30K-60K input tokens. ~$0.15-0.30 per prospectus. Cached on disk
by request hash so reruns are free.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from ipo_portal.deepseek import DeepSeekClient  # noqa: E402


PDF_CACHE = PROJECT_ROOT / "data" / "cache" / "rhp_pdfs"
OUT_ROOT = PROJECT_ROOT / "data" / "site_v2" / "issues"

# Maximum chars per section slice. Each is well under DeepSeek's 64K input
# limit, leaving room for the schema-targeted prompt and system message.
SLICE_CHARS = 90_000


SYSTEM_PROMPT = """You are a senior equity-research analyst extracting structured data from one section of an Indian Red Herring Prospectus (RHP) for display on a financial website (ipowatch.co).

Hard rules — do not break:
1. Output STRICT JSON only. No markdown fences, no prose outside the JSON.
2. Every extracted leaf value carries a provenance block:
   {"value": <the value>, "raw_excerpt": "<verbatim quote, ≤300 chars>", "source_page": <int>, "source_section": "<heading or numbered ref>", "confidence": "high|medium|low"}
3. NEVER fabricate. If a field is not in the supplied text slice, set the leaf to {"value": null, "confidence": "low", "raw_excerpt": null, "source_page": null, "source_section": null}.
3a. `raw_excerpt` must be exact contiguous text copied from the TEXT SLICE. Do not paraphrase, repair grammar, add ellipses, truncate mid-word, or join non-adjacent fragments.
4. Money in **paise** (₹ × 100) as integers. Percentages in **basis points** (1% = 100). Subscription multiples / ratios as decimal strings (4 dp). Shares as plain integers.
5. Dates as ISO 8601 strings.
6. Verbatim quotes win over paraphrase — that's the entire point of this extraction.

The page-range hint tells you the approximate PDF page range this slice covers; use it to populate `source_page` (estimate within the range when the exact page is unclear)."""


LOCATOR_SYSTEM = """You are reading the Table of Contents of an Indian Red Herring Prospectus. Your job: return a JSON map of canonical section names to their starting page numbers.

Output STRICT JSON only — no markdown."""

LOCATOR_USER_TEMPLATE = """Below is the Table of Contents (or front matter) of an RHP. Identify the page number where each canonical section begins. Pages are 1-indexed in the PDF.

REQUIRED OUTPUT:
{{
  "risk_factors": <page or null>,
  "industry_overview": <page or null>,
  "our_business": <page or null>,
  "summary_of_business": <page or null>,
  "objects_of_offer": <page or null>,
  "basis_for_offer_price": <page or null>,
  "capital_structure": <page or null>,
  "financial_information": <page or null>,
  "management_discussion": <page or null>,
  "outstanding_litigation": <page or null>,
  "shareholding_pattern": <page or null>,
  "promoters_and_promoter_group": <page or null>
}}

For each, only report the page if the TOC explicitly states it. If a row isn't in the TOC, set it to null.

TEXT (front matter / TOC):
{text}
"""


# Each section corresponds to one top-level key in
# docs/data/IPO_PAGE_SCHEMA.md.
#
# Slicing strategy (anchor-first):
# 1. The TOC locator pass returns a map of canonical section names ->
#    PDF page numbers (best-effort, may be null for unfamiliar TOCs).
# 2. For each section below, we look up its ``anchor`` in the map. If
#    present, the slice is built around that page (start_page - 1 to
#    start_page + ``pages_after``).
# 3. If the anchor is missing, fall back to ``anchor_fallback_keyword``
#    searched case-insensitively in the document body (skipping the TOC).
# 4. If THAT also fails, fall back to ``absolute_fallback`` (start,
#    length in chars).
#
# Sections that pull cover-page facts (legal name, BRLM, registrar)
# also set ``include_cover=True`` so we always prepend the first 30k
# chars of the PDF (front matter) to the slice. This costs ~30k input
# tokens but guarantees the cover-page facts are in the model's
# context regardless of where the deeper section anchor lands.
SECTIONS: list[dict[str, Any]] = [
    {
        "name": "company_about",
        "anchor": "our_business",
        "anchor_fallback_keyword": "OUR BUSINESS",
        "pages_after": 35,
        "include_cover": True,
        "absolute_fallback": (0, SLICE_CHARS),
        "target": """
{
  "legal_name": {"value": "<from cover>", "raw_excerpt": "...", "source_page": <int>, "source_section": "Cover Page", "confidence": "high"},
  "trade_names": [{"value": "...", "raw_excerpt": "...", "source_page": <int>, "source_section": "...", "confidence": "..."}],
  "cin": {"value": "...", ...},
  "registered_office": {"value": "...", ...},
  "incorporation_date_iso": {"value": "YYYY-MM-DD", ...},
  "sector": {"value": "<plain English>", ...},
  "sub_sector": {"value": "...", ...},
  "core_business_one_line": {"value": "<≤25 words>", ...},
  "core_business_paragraph": {"value": "<verbatim 3-5 sentences from 'Our Business' intro>", ...},
  "history_timeline": [{"year": <int>, "event": "...", "provenance": {"raw_excerpt": "...", "source_page": <int>, "source_section": "...", "confidence": "..."}}],
  "promoters": [{"name": {"value": "...", "provenance": {...}}, "designation": {...}, "background_one_line": {...}}],
  "key_management": [{"name": {...}, "designation": {...}, "tenure_years": {...}, "background_one_line": {...}}],
  "products_services": [{"name": {...}, "share_of_revenue_pct_bps": {...}, "description_one_line": {...}}]
}
""",
    },
    {
        "name": "the_offer",
        "anchor": "objects_of_offer",
        "anchor_fallback_keyword": "OBJECTS OF THE OFFER",
        "pages_after": 25,
        "include_cover": True,
        "absolute_fallback": (0, SLICE_CHARS),
        "target": """
{
  "issue_type": {"value": "Fresh + OFS | Fresh | OFS", ...},
  "is_book_built": {"value": <bool>, ...},
  "fresh_issue": {"shares": {...}, "amount_paise": {...}, "amount_inr_text": {...}},
  "ofs": {"shares": {...}, "amount_paise": {...}, "amount_inr_text": {...}, "selling_shareholders": [{"name": {...}, "shares": {...}, "amount_paise": {...}}]},
  "total_offer": {"shares": {...}, "amount_paise": {...}, "amount_inr_text": {...}},
  "reservation": {"qib_shares": {...}, "nii_shares": {...}, "retail_shares": {...}, "employee_shares": {...}, "shareholder_shares": {...}, "policyholder_shares": {...}},
  "anchor_book": {"anchor_date_iso": {...}, "anchor_portion_paise": {...}, "anchor_shares": {...}, "investor_count": {...}, "top_investors": [{"name": {...}, "shares": {...}, "amount_paise": {...}, "category": {...}}]},
  "use_of_proceeds": [{"purpose": {...}, "amount_paise": {...}, "percent_bps": {...}, "provenance": {"raw_excerpt": "...", "source_page": <int>, "source_section": "Objects of the Offer", "confidence": "..."}}],
  "lead_managers": [{"name": {...}, "role": {...}}],
  "registrar": {"name": {...}, "address": {...}, "email": {...}, "phone": {...}, "website": {...}}
}
""",
    },
    {
        "name": "risks",
        "anchor": "risk_factors",
        "anchor_fallback_keyword": "RISK FACTORS",
        "pages_after": 60,
        "include_cover": False,
        "absolute_fallback": (110_000, 110_000 + SLICE_CHARS * 2),
        "target": """
{
  "total_disclosed_count": {"value": <int>, ...},
  "by_category": [
    {
      "category": "<Operational | Financial | Regulatory | Market | Geographic | Other>",
      "count": <int>,
      "top_risks": [
        {"title": {"value": "<one line>", ...}, "summary": {"value": "<2-3 sentences>", ...}, "severity_inferred": {"value": "high|medium|low", ...}, "risk_factor_number": {"value": "<e.g., 3.2>", ...}, "provenance": {"raw_excerpt": "...", "source_page": <int>, "source_section": "...", "confidence": "..."}}
      ]
    }
  ],
  "qualitative_themes": [{"value": "<one-line>", "provenance": {...}}]
}
""",
    },
    {
        "name": "industry_landscape",
        "anchor": "industry_overview",
        "anchor_fallback_keyword": "INDUSTRY OVERVIEW",
        "pages_after": 50,
        "include_cover": False,
        "absolute_fallback": (200_000, 200_000 + SLICE_CHARS * 2),
        "target": """
{
  "industry_name": {"value": "...", ...},
  "market_size": {"current_paise": {...}, "current_text": {...}, "projected_paise": {...}, "projected_text": {...}, "cagr_bps": {...}, "cagr_period": {...}},
  "global_context": {"global_market_text": {...}, "india_share_text": {...}},
  "key_drivers": [{"value": "<one-line>", "provenance": {...}}],
  "key_headwinds": [{"value": "<one-line>", "provenance": {...}}],
  "competitive_landscape": [
    {"competitor": {...}, "type": {"value": "listed|unlisted|mnc|fragmented", ...}, "revenue_paise": {...}, "market_share_bps": {...}, "notes": {...}}
  ],
  "category_breakdown": [{"category": {...}, "market_share_pct_bps": {...}}],
  "geographic_breakdown": [{"region": {...}, "share_pct_bps": {...}}]
}
""",
    },
    {
        "name": "macros_and_regulatory",
        "anchor": "industry_overview",
        "anchor_fallback_keyword": "INDUSTRY OVERVIEW",
        "pages_after": 50,
        "include_cover": False,
        "absolute_fallback": (200_000, 200_000 + SLICE_CHARS * 2),
        "target": """
{
  "policy_tailwinds": [{"policy": {...}, "year": {...}, "summary": {...}}],
  "regulatory_landscape": {"summary": {...}, "key_regulators": [{"value": "...", "provenance": {...}}]},
  "economic_sensitivity": {"value": "low|medium|high", "rationale": {...}}
}
""",
    },
    {
        "name": "business_model",
        "anchor": "our_business",
        "anchor_fallback_keyword": "OUR BUSINESS",
        "pages_after": 40,
        "include_cover": False,
        "absolute_fallback": (110_000, 110_000 + SLICE_CHARS * 2),
        "target": """
{
  "revenue_model": {"summary": {...}},
  "customer_segments": [{"segment": {...}, "share_of_revenue_bps": {...}}],
  "customer_concentration": {"top_5_share_bps": {...}, "top_10_share_bps": {...}, "notes": {...}},
  "geographic_distribution": [{"region": {...}, "share_of_revenue_bps": {...}}],
  "supplier_concentration": {"top_5_share_bps": {...}, "notes": {...}},
  "competitive_moats": [{"moat": {...}, "evidence": {...}}],
  "key_dependencies": [{"value": "<one-line>", "provenance": {...}}]
}
""",
    },
    {
        "name": "financial_highlights",
        "anchor": "financial_information",
        "anchor_fallback_keyword": "FINANCIAL INFORMATION",
        "pages_after": 80,
        "include_cover": False,
        "absolute_fallback": (500_000, 500_000 + SLICE_CHARS * 3),
        "target": """
{
  "fiscal_periods": [
    {
      "period": {"value": "FY24 | FY23 | FY22 | etc.", ...},
      "period_end": {"value": "YYYY-MM-DD", ...},
      "revenue_paise": {...}, "revenue_inr_text": {...}, "revenue_growth_yoy_bps": {...},
      "ebitda_paise": {...}, "ebitda_margin_bps": {...},
      "pat_paise": {...}, "pat_margin_bps": {...},
      "eps_paise": {...},
      "total_assets_paise": {...}, "total_debt_paise": {...}, "debt_to_equity_x": {...},
      "net_worth_paise": {...},
      "roe_bps": {...}, "roce_bps": {...},
      "cfo_paise": {...}
    }
  ],
  "revenue_cagr_bps": {...}, "revenue_cagr_period": {...},
  "pat_cagr_bps": {...},
  "ebitda_cagr_bps": {...},
  "segment_breakdown": [{"period": {...}, "segment": {...}, "revenue_paise": {...}, "share_bps": {...}}],
  "geographic_revenue": [{"period": {...}, "region": {...}, "revenue_paise": {...}}],
  "key_observations": [{"value": "<one-line>", "provenance": {...}}]
}
""",
    },
    {
        "name": "valuation_peers",
        "anchor": "basis_for_offer_price",
        "anchor_fallback_keyword": "BASIS FOR THE OFFER PRICE",
        "pages_after": 15,
        "include_cover": False,
        "absolute_fallback": (300_000, 300_000 + SLICE_CHARS),
        "target": """
{
  "post_offer_paid_up_paise_at_upper_band": {...},
  "implied_market_cap_at_upper_band_paise": {...},
  "implied_market_cap_inr_text": {...},
  "implied_pe_x_at_upper_band": {...},
  "implied_pb_x_at_upper_band": {...},
  "ev_to_ebitda_at_upper_band": {...},
  "peer_comparison": [
    {"company": {...}, "revenue_paise": {...}, "pat_margin_bps": {...}, "pe_x": {...}, "pb_x": {...}, "market_cap_paise": {...}}
  ],
  "valuation_band_inference": {"value": "premium|in-line|discount", ...},
  "rationale": {"value": "<editorial one-sentence>", "raw_excerpt": null, "source_page": null, "source_section": null, "confidence": "low"}
}
""",
    },
    {
        "name": "promoter_shareholding_litigation",
        "anchor": "capital_structure",
        "anchor_fallback_keyword": "CAPITAL STRUCTURE",
        "pages_after": 60,
        "include_cover": False,
        "absolute_fallback": (123_000, 123_000 + SLICE_CHARS * 2),
        "target": """
{
  "promoter_holding": {"pre_offer_pct_bps": {...}, "post_offer_pct_bps": {...}, "lock_in_period_text": {...}},
  "top_public_shareholders": [{"name": {...}, "stake_bps": {...}, "category": {...}}],
  "shareholding_pattern": [{"period": {...}, "promoter_bps": {...}, "public_bps": {...}}],
  "investor_pedigree_one_line": {...},
  "litigation": {
    "tax_disputes": {"outstanding_paise": {...}, "count": {...}, "notes": {...}},
    "civil_cases": {"outstanding_paise": {...}, "count": {...}, "notes": {...}},
    "criminal_proceedings": {"count": {...}, "notes": {...}},
    "regulatory_actions": {"count": {...}, "notes": {...}},
    "material_litigation": [{"party": {...}, "forum": {...}, "amount_paise": {...}, "status": {...}}]
  },
  "capital_structure": {"face_value_paise": {...}, "pre_offer_paid_up_capital_paise": {...}, "post_offer_paid_up_capital_paise": {...}, "dilution_pct_bps": {...}, "authorised_capital_paise": {...}}
}
""",
    },
]


@dataclass
class PDFText:
    text: str
    pages: list[str]  # one entry per PDF page
    char_to_page: list[int]  # char offset → page number (1-indexed)


def download_pdf(url: str) -> tuple[bytes, Path]:
    url_hash = hashlib.sha256(url.encode()).hexdigest()
    PDF_CACHE.mkdir(parents=True, exist_ok=True)
    path = PDF_CACHE / f"{url_hash[:16]}.pdf"
    if path.exists():
        return path.read_bytes(), path
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=180) as r:
        data = r.read()
    # NSE serves RHPs zipped (e.g. RHP_BMLL.zip). Transparently unwrap the
    # PDF inside so a `.zip` document URL works end-to-end. (BSE/SEBI serve
    # direct PDFs — the magic-byte check leaves those untouched.)
    data = _unwrap_pdf(data, url)
    path.write_bytes(data)
    return data, path


def _unwrap_pdf(data: bytes, url: str) -> bytes:
    """If ``data`` is a ZIP, return the largest PDF member; else return as-is."""
    if data[:4] != b"PK\x03\x04":
        return data
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        pdf_members = [n for n in zf.namelist() if n.lower().endswith(".pdf")]
        if not pdf_members:
            raise ValueError(f"ZIP at {url} contains no PDF member: {zf.namelist()[:5]}")
        # The RHP is the largest PDF in the archive (forms/ratios are smaller).
        biggest = max(pdf_members, key=lambda n: zf.getinfo(n).file_size)
        return zf.read(biggest)


def extract_pdf_text(pdf_path: Path) -> PDFText:
    """Run pdftotext preserving form-feed page delimiters."""
    raw = subprocess.check_output(["pdftotext", "-layout", str(pdf_path), "-"])
    text = raw.decode("utf-8", errors="replace")
    # pdftotext emits \f between pages.
    pages = text.split("\f")
    # Build char_to_page map; we'll consult it for any char offset.
    char_to_page: list[int] = []
    for page_idx, page_text in enumerate(pages, start=1):
        char_to_page.extend([page_idx] * (len(page_text) + 1))  # +1 for the \f
    return PDFText(text=text, pages=pages, char_to_page=char_to_page)


def find_section_start(text: str, keyword: str, search_offset: int = 0) -> int | None:
    """Locate the first occurrence of an RHP section keyword.

    RHP section headings are typically all-caps. We search case-insensitive
    after ``search_offset`` (front matter aliases of headings are common
    — e.g., a Table of Contents lists "RISK FACTORS" before the section).
    """
    haystack = text[search_offset:].upper()
    idx = haystack.find(keyword.upper())
    if idx < 0:
        return None
    return search_offset + idx


def page_to_char(pdf: PDFText, page: int) -> int:
    """Return the char offset where 1-indexed ``page`` begins."""
    if page <= 1:
        return 0
    if page > len(pdf.pages):
        return len(pdf.text)
    return sum(len(pdf.pages[i]) + 1 for i in range(page - 1))


def slice_for_section(
    pdf: PDFText,
    section: dict[str, Any],
    locator: dict[str, Any] | None,
) -> tuple[str, int, int, str]:
    """Pick the best slice for ``section`` given the locator map.

    Returns ``(text, start_char, end_char, slice_method)`` where
    ``slice_method`` is one of ``"locator"``, ``"keyword"``, ``"absolute"``
    so the caller can record provenance.

    Anchor resolution order:
    1. locator map (TOC-derived page number)
    2. fallback all-caps keyword search past the TOC
    3. absolute char-range fallback (last resort)
    """
    anchor_name = section.get("anchor")
    fallback_keyword = section.get("anchor_fallback_keyword")
    pages_after = int(section.get("pages_after", 30))
    include_cover = bool(section.get("include_cover"))
    absolute_fallback = section.get("absolute_fallback")

    anchor_char: int | None = None
    method = "absolute"

    if locator and anchor_name:
        page = locator.get(anchor_name)
        if isinstance(page, int) and page > 0:
            anchor_char = page_to_char(pdf, page)
            method = "locator"

    if anchor_char is None and fallback_keyword:
        idx = find_section_start(pdf.text, fallback_keyword, search_offset=30_000)
        if idx is not None:
            anchor_char = idx
            method = "keyword"

    if anchor_char is None and absolute_fallback:
        start, end = absolute_fallback
        start = max(0, start)
        end = min(len(pdf.text), end)
        return pdf.text[start:end], start, end, "absolute"

    # Build slice around the anchor.
    anchor_char = anchor_char or 0
    span_chars_per_page = max(2_500, len(pdf.text) // max(len(pdf.pages), 1))
    end_char = min(anchor_char + pages_after * span_chars_per_page, len(pdf.text))

    # Optionally prepend cover-page material so cover-only facts
    # (legal name, BRLM, registrar) are always available.
    cover_chunk = pdf.text[:30_000] if include_cover else ""
    body = pdf.text[anchor_char:end_char]

    if cover_chunk and anchor_char > len(cover_chunk):
        # Two-part slice: cover_chunk + body
        combined = cover_chunk + "\n\n... [SKIP TO ANCHOR] ...\n\n" + body
        # Report char range using the body's actual offsets (cover prepended for context only).
        return combined, anchor_char, end_char, method

    return body, anchor_char, end_char, method


def page_at_char(pdf: PDFText, char_offset: int) -> int:
    """Return the 1-indexed page number for a char offset (clamped)."""
    if char_offset < 0:
        return 1
    if char_offset >= len(pdf.char_to_page):
        return len(pdf.pages)
    return pdf.char_to_page[char_offset]


def locate_sections(pdf: PDFText, client: DeepSeekClient, slug: str) -> dict[str, Any]:
    """Cheap one-shot pass: ask DeepSeek to map canonical section names
    to PDF page numbers using the TOC / front matter.

    Returns ``{}`` if the locator fails — callers fall back to keyword
    search and absolute offsets, so this is a quality boost, not a hard
    dependency.
    """
    front_matter = pdf.text[:40_000]
    response = client.chat(
        user=LOCATOR_USER_TEMPLATE.format(text=front_matter),
        system=LOCATOR_SYSTEM,
        response_format="json_object",
        purpose=f"rich_rhp:{slug}:locator",
        max_tokens=1500,
    )
    body = response.json_content
    if not isinstance(body, dict):
        return {}
    return body


def run(url: str, slug: str, *, write: bool = True) -> dict[str, Any]:
    client = DeepSeekClient()
    print(f"[rich-rhp] downloading {url}", flush=True)
    pdf_bytes, pdf_path = download_pdf(url)
    print(f"[rich-rhp] pdf: {len(pdf_bytes):,} bytes at {pdf_path}", flush=True)

    print("[rich-rhp] extracting text…", flush=True)
    pdf = extract_pdf_text(pdf_path)
    print(f"[rich-rhp] text: {len(pdf.text):,} chars across {len(pdf.pages)} pages", flush=True)

    print("[rich-rhp] locating sections (TOC pre-pass)…", flush=True)
    locator = locate_sections(pdf, client, slug)
    if locator:
        print("[rich-rhp] TOC anchors:", {k: v for k, v in locator.items() if v}, flush=True)
    else:
        print("[rich-rhp] TOC anchors: (none found — falling back to keyword search)", flush=True)

    extractions: dict[str, Any] = {}
    tokens: dict[str, dict[str, Any]] = {}
    total_cost = 0.0

    for section in SECTIONS:
        name = section["name"]
        slice_text, start, end, slice_method = slice_for_section(pdf, section, locator)
        start_page = page_at_char(pdf, start)
        end_page = page_at_char(pdf, end - 1)
        print(
            f"[rich-rhp] section '{name}': method={slice_method}, "
            f"chars {start:,}-{end:,} (pages {start_page}-{end_page}), "
            f"slice_len={len(slice_text):,}",
            flush=True,
        )
        user_msg = (
            f"SECTION TARGET: {name}\n"
            f"PDF PAGE RANGE FOR THIS SLICE: pages {start_page} to {end_page} (1-indexed). "
            f"Use this range to fill source_page on every provenance block.\n\n"
            f"REQUIRED OUTPUT SHAPE (fill every leaf with the provenance block):\n"
            f"{section['target']}\n\n"
            f"TEXT SLICE:\n{slice_text}\n"
        )
        response = client.chat(
            user=user_msg,
            system=SYSTEM_PROMPT,
            response_format="json_object",
            purpose=f"rich_rhp:{slug}:{name}",
            extra_telemetry={
                "section": name,
                "char_count": len(slice_text),
                "page_start": start_page,
                "page_end": end_page,
            },
        )
        extractions[name] = response.json_content
        tokens[name] = {
            "in": response.prompt_tokens,
            "out": response.completion_tokens,
            "cached": response.cached,
            "page_range": [start_page, end_page],
        }
        total_cost += response.estimated_cost_usd
        print(
            f"[rich-rhp] '{name}' done: in={response.prompt_tokens}, "
            f"out={response.completion_tokens}, cached={response.cached}",
            flush=True,
        )

    # Synthesize a `hero` block + `meta` block from the section outputs.
    hero = build_hero(extractions)
    meta = build_meta(
        url=url,
        pdf_path=pdf_path,
        pdf_bytes=pdf_bytes,
        pdf=pdf,
        tokens=tokens,
        total_cost_usd=total_cost,
    )

    document = {
        "$schema": "https://ipo-watch.local/schema/v2/ipo_page.schema.json",
        "schema_version": "2.0.0",
        "slug": slug,
        "meta": meta,
        "hero": hero,
        "company_about": extractions.get("company_about"),
        "the_offer": extractions.get("the_offer"),
        "industry_landscape": extractions.get("industry_landscape"),
        "macros": extractions.get("macros_and_regulatory"),
        "business_model": extractions.get("business_model"),
        "financial_highlights": extractions.get("financial_highlights"),
        "valuation": extractions.get("valuation_peers"),
        "risks": extractions.get("risks"),
        "promoter_and_shareholding_and_litigation": extractions.get(
            "promoter_shareholding_litigation"
        ),
    }
    citation_report = repair_and_validate_citations(document, pdf)
    document.setdefault("meta", {})["citation_validation"] = citation_report
    if citation_report["state"] == "failed":
        examples = citation_report["unresolved"][:5]
        raise RuntimeError(f"RHP citation validation failed for {slug}: {examples}")

    if write:
        out_dir = OUT_ROOT / slug
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "prospectus.json"
        out_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"[rich-rhp] written: {out_path}", flush=True)
        print(f"[rich-rhp] total cost: ${total_cost:.4f}", flush=True)

    return document


def repair_and_validate_citations(document: dict[str, Any], pdf: PDFText) -> dict[str, Any]:
    """Verify every provenance raw_excerpt against its cited PDF page.

    If the excerpt exists elsewhere in the PDF, correct ``source_page``.
    If it cannot be found anywhere, redact that leaf to null. Wrong is worse
    than missing: a partial clean extraction is publishable, but a fabricated
    citation is not.
    """
    repaired: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for path, leaf in _iter_citation_leaves(document):
        excerpt = leaf.get("raw_excerpt")
        page = leaf.get("source_page")
        if not isinstance(excerpt, str) or not excerpt.strip():
            continue
        if isinstance(page, int) and _excerpt_on_page(pdf, excerpt, page):
            continue
        found = _find_excerpt_page(pdf, excerpt)
        if found is None:
            _redact_unresolved_leaf(leaf)
            unresolved.append({"path": path, "source_page": page, "raw_excerpt": excerpt[:160]})
            continue
        if page != found:
            leaf["source_page"] = found
            repaired.append({"path": path, "old_page": page, "new_page": found})
    return {
        "state": "clean_with_redactions" if unresolved else "clean",
        "checked_count": sum(1 for _ in _iter_citation_leaves(document)),
        "repaired_count": len(repaired),
        "unresolved_count": len(unresolved),
        "redacted_count": len(unresolved),
        "repaired": repaired[:200],
        "unresolved": unresolved[:200],
    }


def _redact_unresolved_leaf(leaf: dict[str, Any]) -> None:
    if "value" in leaf:
        leaf["value"] = None
    leaf["raw_excerpt"] = None
    leaf["source_page"] = None
    leaf["source_section"] = None
    leaf["confidence"] = "low"


def _iter_citation_leaves(obj: Any, path: str = ""):
    if isinstance(obj, dict):
        if "raw_excerpt" in obj and "source_page" in obj:
            yield path or "$", obj
        for key, value in obj.items():
            child = f"{path}.{key}" if path else str(key)
            yield from _iter_citation_leaves(value, child)
    elif isinstance(obj, list):
        for idx, value in enumerate(obj):
            yield from _iter_citation_leaves(value, f"{path}[{idx}]")


def _excerpt_on_page(pdf: PDFText, excerpt: str, page: int) -> bool:
    if page < 1 or page > len(pdf.pages):
        return False
    return _normalize_for_match(excerpt) in _normalize_for_match(pdf.pages[page - 1])


def _find_excerpt_page(pdf: PDFText, excerpt: str) -> int | None:
    needle = _normalize_for_match(excerpt)
    if not needle:
        return None
    for idx, page_text in enumerate(pdf.pages, start=1):
        if needle in _normalize_for_match(page_text):
            return idx
    return None


def _normalize_for_match(text: str) -> str:
    return " ".join(str(text).replace("\u00a0", " ").split()).casefold()


def build_hero(extractions: dict[str, Any]) -> dict[str, Any]:
    """Pull a compact hero block from the section extractions."""
    company_about = extractions.get("company_about") or {}
    the_offer = extractions.get("the_offer") or {}
    return {
        "headline_pitch": _leaf(company_about, "core_business_one_line"),
        "legal_name": _leaf(company_about, "legal_name"),
        "issue_type": _leaf(the_offer, "issue_type"),
        "total_offer_paise": _nested(the_offer, "total_offer.amount_paise"),
        "total_offer_inr_text": _nested(the_offer, "total_offer.amount_inr_text"),
        "use_of_proceeds_count": len(the_offer.get("use_of_proceeds") or []),
        "anchor_investor_count": _nested(the_offer, "anchor_book.investor_count"),
        "lead_managers": [_leaf(lm, "name") for lm in the_offer.get("lead_managers") or []],
    }


def build_meta(
    *,
    url: str,
    pdf_path: Path,
    pdf_bytes: bytes,
    pdf: PDFText,
    tokens: dict[str, dict[str, Any]],
    total_cost_usd: float,
) -> dict[str, Any]:
    return {
        "rhp_pdf_url": url,
        "rhp_pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
        "rhp_pdf_local_path": str(pdf_path),
        "rhp_pdf_pages": len(pdf.pages),
        "raw_text_chars": len(pdf.text),
        "extraction_method": "deepseek-multipass-v1",
        "extracted_at_iso": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "extraction_passes": [
            {"section": k, **v} for k, v in tokens.items()
        ],
        "total_cost_usd": round(total_cost_usd, 4),
    }


def _leaf(parent: Any, key: str) -> Any:
    if not isinstance(parent, dict):
        return None
    return parent.get(key)


def _nested(parent: Any, path: str) -> Any:
    current: Any = parent
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default="https://nsearchives.nseindia.com/emerge/corporates/content/VasaDenticity_RHP.pdf",
    )
    parser.add_argument("--slug", default="vasa-denticity-test")
    args = parser.parse_args()
    run(args.url, args.slug)
    return 0


if __name__ == "__main__":
    sys.exit(main())
