"""Test the RHP-insights extraction end-to-end on a real prospectus.

Picks a known RHP URL, downloads, runs pdftotext, slices the prospectus
into four targeted sections (front matter / risks / industry+business /
financials), and runs one DeepSeek call per section with a focused
prompt. Synthesizes the four outputs into a single insights document.

Why four passes? An Indian RHP is 300–900 pages. A single 64K-token
input call can't carry the whole prospectus. Sequential targeted calls
let us spend tokens where the structured data actually lives.

Output: ``data/reports/rhp_test_<slug>.json`` with the synthesized
insights, costs, and section excerpts for audit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from ipo_portal.deepseek import DeepSeekClient


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = PROJECT_ROOT / "data" / "reports"


SYSTEM_PROMPT_BASE = """You are a senior equity-research analyst writing a structured insights brief on an Indian IPO from its Red Herring Prospectus (RHP).

Output STRICT JSON only — no markdown fences, no commentary outside the JSON. Be conservative: any field you can't confidently extract from the supplied text, return null. Never fabricate financials or quotes.

The supplied text is one section-slice of a longer prospectus. Extract only what this slice contains; trust other slices to cover other sections. Use Indian conventions:
- Money in **paise** (₹ × 100) as integers.
- Percentages in basis points (1% = 100).
- Dates as ISO 8601 strings.
- Shares as plain integers."""


SECTIONS: list[dict[str, Any]] = [
    {
        "name": "front_matter",
        "char_start": 0,
        "char_end": 80_000,
        "schema_hint": """
{
  "company": {
    "legal_name": "<from cover>",
    "trade_name": "<if different>",
    "cin": "<from cover>",
    "registered_office": "<from cover>",
    "promoters": ["<name>", "..."],
    "incorporation_date": "<ISO date>",
    "sector": "<plain English>",
    "core_business_one_line": "<plain English>"
  },
  "offer_details": {
    "issue_type": "Fresh + OFS | Fresh | OFS",
    "fresh_issue_paise": <integer or null>,
    "ofs_paise": <integer or null>,
    "total_offer_paise": <integer or null>,
    "fresh_shares": <integer>,
    "ofs_shares": <integer>,
    "total_shares": <integer>,
    "face_value_paise": <integer>,
    "lot_size_shares": <integer or null>,
    "is_book_built": <true|false>
  },
  "lead_managers": [{"name": "...", "role": "BRLM | book runner | lead manager"}],
  "registrar": {"name": "...", "address": "...", "email": "...", "phone": "...", "website": "..."},
  "key_dates": {
    "drhp_filed": "<date>", "rhp_filed": "<date>",
    "open_date": "<date>", "close_date": "<date>",
    "anchor_date": "<date>", "allotment_date": "<date>",
    "refund_date": "<date>", "listing_date": "<date>"
  },
  "anchor_summary": {
    "anchor_portion_paise": <int>, "anchor_shares": <int>,
    "anchor_investor_count": <int>,
    "top_anchors": [{"name": "...", "shares": <int>, "amount_paise": <int>}]
  },
  "use_of_proceeds": [
    {"purpose": "<plain English>", "amount_paise": <int>, "percent_bps": <int>}
  ],
  "warnings": ["<freeform: anything in this slice that is unclear>"]
}
""",
    },
    {
        "name": "risks",
        "char_start": 60_000,
        "char_end": 200_000,
        "schema_hint": """
{
  "risk_factors": {
    "total_count_disclosed": <int>,
    "categories": [
      {"category": "<e.g., regulatory, operational, financial, market, geographic>",
       "count_in_category": <int>,
       "top_risks": [
         {"title": "<one-line>", "summary": "<2-3 sentences>", "severity_inferred": "high|medium|low"}
       ]}
    ],
    "qualitative_themes": ["<freeform investor-relevant signal>"]
  },
  "litigation_summary": {
    "tax_disputes_outstanding_paise": <int or null>,
    "civil_cases_outstanding_paise": <int or null>,
    "criminal_proceedings_count": <int or null>,
    "regulatory_actions_count": <int or null>,
    "notes": "<freeform>"
  },
  "warnings": ["..."]
}
""",
    },
    {
        "name": "industry_business",
        "char_start": 200_000,
        "char_end": 500_000,
        "schema_hint": """
{
  "industry_overview": {
    "industry_name": "<plain English>",
    "market_size_paise": <int or null>,
    "market_size_text": "<verbatim from prospectus, e.g. '₹50,000 Cr by 2027'>",
    "growth_rate_cagr_bps": <int or null>,
    "growth_period": "<e.g. '2024-2027'>",
    "key_drivers": ["<one-line>", "..."],
    "key_headwinds": ["<one-line>", "..."],
    "competitive_landscape": [
      {"competitor": "...", "type": "listed|unlisted|public-sector|MNC|fragmented", "note": "..."}
    ]
  },
  "macro_context": {
    "policy_tailwinds": ["<plain English>", "..."],
    "regulatory_landscape": "<2-3 sentences>",
    "economic_sensitivity": "<low|medium|high — and why>"
  },
  "business_model": {
    "revenue_model": "<plain English, 2-3 sentences>",
    "customer_segments": ["<segment>", "..."],
    "geographic_concentration": "<plain English>",
    "key_competitive_advantages": ["<one-line>", "..."],
    "key_dependencies": ["<one-line>", "..."]
  },
  "warnings": ["..."]
}
""",
    },
    {
        "name": "financials",
        "char_start": 500_000,
        "char_end": -120_000,  # last 120k chars
        "schema_hint": """
{
  "financial_highlights": {
    "fiscal_periods": [
      {"period": "FY24 | FY23 | FY22 | H1FY25 | etc.",
       "revenue_paise": <int>, "ebitda_paise": <int>,
       "ebitda_margin_bps": <int>,
       "pat_paise": <int>, "pat_margin_bps": <int>,
       "eps_paise": <int>,
       "roe_bps": <int>, "roce_bps": <int>,
       "total_assets_paise": <int>, "total_debt_paise": <int>,
       "net_worth_paise": <int>, "debt_to_equity_x": <decimal as string>
      }
    ],
    "revenue_cagr_bps": <int or null>,
    "pat_cagr_bps": <int or null>,
    "cagr_period": "<e.g. 'FY22-FY24'>"
  },
  "valuation_signals": {
    "pre_offer_paid_up_paise": <int>,
    "post_offer_paid_up_paise_at_upper_band": <int or null>,
    "implied_pe_at_upper_band_x": "<decimal as string or null>",
    "implied_pb_at_upper_band_x": "<decimal as string or null>",
    "peer_comparison": [
      {"company": "...", "pe_x": "<decimal as string>", "pb_x": "<decimal as string>",
       "revenue_paise": <int>, "pat_margin_bps": <int>}
    ]
  },
  "promoter_holding": {
    "pre_offer_pct_bps": <int>, "post_offer_pct_bps": <int>
  },
  "warnings": ["..."]
}
""",
    },
]


SYNTHESIS_PROMPT = """You are writing the executive summary on top of four section-extractions from one RHP. Synthesize a concise, investor-grade single JSON brief.

Reject any duplicates across sections; for fields present in multiple sections, prefer the more specific value. If sections disagree on a number, set the value AND record both observations in `disagreements[]`.

Output STRICT JSON only:

{{
  "headline": "<one-line investor pitch>",
  "summary_paragraph": "<3-5 sentence neutral overview of company, offer, why it matters>",
  "investor_thesis_positives": ["<one-line>", "..."],
  "investor_thesis_concerns": ["<one-line>", "..."],
  "key_numbers": {{
    "total_offer_paise": <int or null>,
    "offer_paise_display": "<₹ X Cr>",
    "issue_type": "...",
    "lot_size_shares": <int or null>,
    "implied_market_cap_at_upper_band_paise": <int or null>,
    "implied_pe_x": "<decimal as string or null>",
    "promoter_holding_post_offer_pct_bps": <int or null>
  }},
  "company_card": <object from front_matter.company>,
  "offer_card": <object from front_matter.offer_details>,
  "industry_card": <subset of industry_business.industry_overview>,
  "financials_card": <best fiscal period from financials.financial_highlights>,
  "top_risks": ["<one-line>", "..."],
  "macros": <object from industry_business.macro_context>,
  "anchors_summary_one_line": "<plain English>",
  "use_of_proceeds_one_line": "<plain English>",
  "disagreements": [],
  "model_certainty": "high | medium | low",
  "warnings": ["..."]
}}

SECTION EXTRACTIONS (raw):
{sections_blob}
"""


def download_pdf(url: str, dest: Path) -> bytes:
    if dest.exists():
        return dest.read_bytes()
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return data


def extract_text(pdf_path: Path) -> str:
    import subprocess
    out = subprocess.check_output(["pdftotext", "-layout", str(pdf_path), "-"])
    return out.decode("utf-8", errors="replace")


def slice_text(text: str, start: int, end: int) -> str:
    if end < 0:
        end = max(start, len(text) + end)
    return text[start:end]


def run(url: str, slug: str, out_root: Path) -> Path:
    client = DeepSeekClient()
    pdf_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
    pdf_path = PROJECT_ROOT / "data" / "cache" / "rhp_pdfs" / f"{pdf_hash}.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[rhp-test] downloading {url}", flush=True)
    pdf_bytes = download_pdf(url, pdf_path)
    print(f"[rhp-test] pdf: {len(pdf_bytes):,} bytes", flush=True)

    print("[rhp-test] extracting text…", flush=True)
    text = extract_text(pdf_path)
    print(f"[rhp-test] text: {len(text):,} chars", flush=True)

    extractions: dict[str, Any] = {}
    section_costs: dict[str, float] = {}
    section_tokens: dict[str, dict[str, int]] = {}

    for section in SECTIONS:
        name = section["name"]
        slice_ = slice_text(text, section["char_start"], section["char_end"])
        print(f"[rhp-test] section '{name}': {len(slice_):,} chars", flush=True)
        user = (
            f"SECTION: {name}\n\n"
            f"REQUIRED OUTPUT SHAPE:\n{section['schema_hint']}\n\n"
            f"TEXT SLICE:\n{slice_}\n"
        )
        response = client.chat(
            user=user,
            system=SYSTEM_PROMPT_BASE,
            response_format="json_object",
            purpose=f"rhp_test:{slug}:{name}",
            extra_telemetry={"section": name, "char_count": len(slice_)},
        )
        extractions[name] = response.json_content
        section_costs[name] = response.estimated_cost_usd
        section_tokens[name] = {
            "in": response.prompt_tokens,
            "out": response.completion_tokens,
            "cached": response.cached,
        }
        print(
            f"[rhp-test] '{name}' done: in={response.prompt_tokens}, "
            f"out={response.completion_tokens}, cached={response.cached}",
            flush=True,
        )

    print("[rhp-test] synthesizing summary…", flush=True)
    synth_response = client.chat(
        user=SYNTHESIS_PROMPT.format(sections_blob=json.dumps(extractions, ensure_ascii=False)),
        system="You are a senior equity-research editor. Output strict JSON.",
        response_format="json_object",
        purpose=f"rhp_test:{slug}:synthesis",
    )
    section_costs["synthesis"] = synth_response.estimated_cost_usd
    section_tokens["synthesis"] = {
        "in": synth_response.prompt_tokens,
        "out": synth_response.completion_tokens,
        "cached": synth_response.cached,
    }

    total_cost = sum(section_costs.values())
    print(f"[rhp-test] total cost: ${total_cost:.4f}", flush=True)

    out_root.mkdir(parents=True, exist_ok=True)
    out_path = out_root / f"rhp_test_{slug}.json"
    out_path.write_text(
        json.dumps(
            {
                "url": url,
                "slug": slug,
                "pdf_bytes": len(pdf_bytes),
                "text_chars": len(text),
                "sections": extractions,
                "synthesis": synth_response.json_content,
                "costs": {
                    "section_usd": section_costs,
                    "total_usd": total_cost,
                },
                "tokens": section_tokens,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"[rhp-test] written: {out_path}", flush=True)
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default="https://nsearchives.nseindia.com/emerge/corporates/content/VasaDenticity_RHP.pdf",
    )
    parser.add_argument("--slug", default="vasa-denticity")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    out = run(args.url, args.slug, args.out)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
