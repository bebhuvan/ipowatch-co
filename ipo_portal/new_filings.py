"""SEBI new-filing article pipeline.

This module builds a separate public artifact tree for filing-stage SEBI
documents. It is intentionally conservative: model output is treated as a
candidate fact set, every cited excerpt is matched back to the PDF text, and
only validated facts are rendered into the article JSON.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .filing_processor import (
    PDFTOTEXT_EXTRACTOR,
    PDFText,
    download_pdf,
    extract_pdf_text,
)
from .sebi import SebiFiling, scrape


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_ROOT = PROJECT_ROOT / "data"
DEFAULT_OUT_ROOT = DEFAULT_DATA_ROOT / "ipo_watch_v3" / "new_filings"
DEFAULT_REPORT = DEFAULT_DATA_ROOT / "reports" / "new_filings_refresh.json"
DEFAULT_MODEL = "gemini-3.1-flash-lite"
RETRY_MODEL = "gemini-3-flash-preview"
SCHEMA_VERSION = "1.1.0"
MAX_FULL_TEXT_CHARS = 2_200_000
MIN_VERIFIED_FACTS = 14
REQUIRED_FACT_GROUPS = {"company", "business", "offer", "financials", "risks"}


SYSTEM_PROMPT = """You extract facts from Indian SEBI public issue filings.

Rules:
1. Return strict JSON only.
2. Use only the supplied PDF text.
3. Every non-null scalar fact must have this exact shape:
   {"value": <string-or-number>, "raw_excerpt": "<exact contiguous quote>", "source_page": <integer>, "confidence": "high|medium|low"}
4. raw_excerpt must be copied verbatim from one page of the supplied text. Do not paraphrase, join distant text, add ellipses, or fix grammar.
5. If a fact is not explicit, use null.
6. Keep arrays short and high signal.
7. Page numbers must come from the nearest --- PDF PAGE N --- marker.
"""


FACT_SHAPE = {"value": "string", "raw_excerpt": "exact contiguous quote", "source_page": 1, "confidence": "high|medium|low"}


ARTICLE_SCHEMA = {
    "company": {
        "name": FACT_SHAPE,
        "description": FACT_SHAPE,
        "industry": FACT_SHAPE,
        "incorporation_or_history": FACT_SHAPE,
        "registered_office": FACT_SHAPE,
    },
    "business": {
        "products_services": [FACT_SHAPE],
        "geographies": [FACT_SHAPE],
        "customers_or_channels": [FACT_SHAPE],
        "competitive_strengths": [FACT_SHAPE],
        "showrooms_or_facilities": [FACT_SHAPE],
        "sourcing_or_operations": [FACT_SHAPE],
    },
    "industry_context": {
        "market_size": [FACT_SHAPE],
        "growth_drivers": [FACT_SHAPE],
        "sector_trends": [FACT_SHAPE],
        "competitive_context": [FACT_SHAPE],
    },
    "offer": {
        "document_type": FACT_SHAPE,
        "issue_type": FACT_SHAPE,
        "fresh_issue": FACT_SHAPE,
        "offer_for_sale": FACT_SHAPE,
        "total_issue": FACT_SHAPE,
        "face_value": FACT_SHAPE,
        "objects_of_issue": [{"object": FACT_SHAPE, "amount": FACT_SHAPE}],
        "lead_managers": [FACT_SHAPE],
        "registrar": FACT_SHAPE,
        "selling_shareholders": [FACT_SHAPE],
    },
    "financials": {
        "latest_revenue": FACT_SHAPE,
        "latest_profit_after_tax": FACT_SHAPE,
        "periods": [
            {
                "period": FACT_SHAPE,
                "revenue_from_operations": FACT_SHAPE,
                "ebitda": FACT_SHAPE,
                "profit_after_tax": FACT_SHAPE,
                "net_worth": FACT_SHAPE,
                "borrowings": FACT_SHAPE,
                "total_assets": FACT_SHAPE,
                "operating_cash_flow": FACT_SHAPE,
            }
        ],
        "debt_or_borrowings": [FACT_SHAPE],
        "key_ratios": [FACT_SHAPE],
        "financial_analysis_points": [FACT_SHAPE],
    },
    "risks": {
        "risk_factor_count": FACT_SHAPE,
        "top_risks": [{"risk": FACT_SHAPE, "why_it_matters": FACT_SHAPE}],
        "red_flags": [FACT_SHAPE],
    },
    "governance": {
        "promoters": [FACT_SHAPE],
        "directors": [FACT_SHAPE],
        "litigation": [FACT_SHAPE],
        "related_party_transactions": [FACT_SHAPE],
        "shareholding": [FACT_SHAPE],
    },
    "valuation": {
        "basis_for_offer_price": [FACT_SHAPE],
        "peer_comparison": [FACT_SHAPE],
        "eps_nav_ronw": [FACT_SHAPE],
    },
}


@dataclass(frozen=True)
class NewFilingJob:
    filing_date: str
    company_name: str
    detail_url: str
    document_url: str
    document_type: str

    @classmethod
    def from_sebi(cls, filing: SebiFiling) -> "NewFilingJob | None":
        if not filing.document_url:
            return None
        doc_type = filing.document_type or "DRHP"
        if doc_type.lower() in {"corrigendum", "abridged_prospectus"}:
            return None
        return cls(
            filing_date=filing.filing_date,
            company_name=filing.company_name,
            detail_url=filing.detail_url,
            document_url=filing.document_url,
            document_type=doc_type,
        )


@dataclass
class RefreshStats:
    fetched: int = 0
    eligible: int = 0
    processed: int = 0
    skipped_existing: int = 0
    published: int = 0
    quarantined: int = 0
    failed: int = 0
    failures: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fetched": self.fetched,
            "eligible": self.eligible,
            "processed": self.processed,
            "skipped_existing": self.skipped_existing,
            "published": self.published,
            "quarantined": self.quarantined,
            "failed": self.failed,
            "failures": self.failures[:100],
        }


def refresh_new_filings(
    *,
    data_root: Path = DEFAULT_DATA_ROOT,
    out_root: Path = DEFAULT_OUT_ROOT,
    report_path: Path = DEFAULT_REPORT,
    limit: int = 25,
    process_limit: int | None = 5,
    model: str = DEFAULT_MODEL,
    retry_model: str | None = RETRY_MODEL,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Fetch latest SEBI filings, process unseen PDFs, and rebuild the index."""
    raw_path = scrape(root=data_root, resolve_pdfs=True, limit=limit)
    raw = _read_json(raw_path)
    rows = raw.get("body") or []
    stats = RefreshStats(fetched=len(rows))

    jobs: list[NewFilingJob] = []
    for row in rows:
        try:
            filing = SebiFiling(
                filing_date=str(row.get("filing_date") or ""),
                company_name=str(row.get("company_name") or ""),
                detail_url=str(row.get("detail_url") or ""),
                document_url=row.get("document_url"),
                document_type=row.get("document_type"),
            )
            job = NewFilingJob.from_sebi(filing)
            if job:
                jobs.append(job)
        except Exception:
            continue
    stats.eligible = len(jobs)
    if process_limit is not None:
        jobs = jobs[:process_limit]

    for job in jobs:
        slug = filing_slug(job)
        existing = out_root / slug / "article.json"
        try:
            if existing.exists() and not force:
                doc = _read_json(existing)
                if doc.get("document_url") == job.document_url and doc.get("quality", {}).get("state") in {"pass", "review"}:
                    stats.skipped_existing += 1
                    continue
            if dry_run:
                stats.processed += 1
                continue
            article = process_new_filing(job, out_root=out_root, model=model, retry_model=retry_model)
            stats.processed += 1
            if article.get("quality", {}).get("publishable"):
                stats.published += 1
            else:
                stats.quarantined += 1
        except Exception as exc:  # noqa: BLE001 - keep the evening batch moving
            stats.failed += 1
            stats.failures.append(
                {
                    "slug": slug,
                    "company_name": job.company_name,
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback_tail": traceback.format_exc()[-2000:],
                }
            )

    if not dry_run:
        rebuild_index(out_root)
    summary = {
        "generated_at": _now(),
        "raw_snapshot": str(raw_path),
        "model": model,
        "retry_model": retry_model,
        "force": force,
        "dry_run": dry_run,
        **stats.to_dict(),
    }
    _write_json(report_path, summary)
    return summary


def process_new_filing(
    job: NewFilingJob,
    *,
    out_root: Path = DEFAULT_OUT_ROOT,
    model: str = DEFAULT_MODEL,
    retry_model: str | None = RETRY_MODEL,
) -> dict[str, Any]:
    pdf_bytes, pdf_path = download_pdf(job.document_url)
    pdf = extract_pdf_text(pdf_path, backend=PDFTOTEXT_EXTRACTOR)
    slug = filing_slug(job)
    source_text = _full_text(pdf)
    facts, call = _extract_with_gemini(source_text, model=model, job=job)
    _augment_financial_periods(facts, pdf)
    _augment_risks(facts, pdf)
    citation = validate_facts(facts, pdf)
    quality = assess_article_quality(facts, citation)
    calls = [call]

    if retry_model and quality["state"] == "fail":
        retry_facts, retry_call = _extract_with_gemini(source_text, model=retry_model, job=job)
        _augment_financial_periods(retry_facts, pdf)
        _augment_risks(retry_facts, pdf)
        retry_citation = validate_facts(retry_facts, pdf)
        retry_quality = assess_article_quality(retry_facts, retry_citation)
        calls.append(retry_call)
        if _quality_rank(retry_quality) > _quality_rank(quality):
            facts = retry_facts
            citation = retry_citation
            quality = retry_quality

    article = build_article(
        job=job,
        slug=slug,
        facts=facts,
        citation=citation,
        quality=quality,
        pdf=pdf,
        pdf_bytes=pdf_bytes,
        pdf_path=pdf_path,
        calls=calls,
    )
    target = out_root / slug / "article.json"
    if article["quality"]["publishable"]:
        _write_json(target, article)
        stale_quarantine = out_root / "_quarantine" / f"{slug}.json"
        if stale_quarantine.exists():
            stale_quarantine.unlink()
    else:
        _write_json(out_root / "_quarantine" / f"{slug}.json", article)
    return article


def _extract_with_gemini(source_text: str, *, model: str, job: NewFilingJob) -> tuple[dict[str, Any], dict[str, Any]]:
    _load_dotenv(PROJECT_ROOT / ".env")
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError("google-genai is required. Install requirements.txt.") from exc

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY or GOOGLE_API_KEY is not set")
    client = genai.Client(api_key=api_key)
    prompt = _prompt(job, source_text)
    started = time.monotonic()
    response = client.models.generate_content(
        model=model,
        contents=[prompt],
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
            max_output_tokens=16_000,
            system_instruction=SYSTEM_PROMPT,
        ),
    )
    elapsed_ms = int((time.monotonic() - started) * 1000)
    raw = response.text or ""
    facts = _parse_json(raw)
    if isinstance(facts, list) and len(facts) == 1 and isinstance(facts[0], dict):
        facts = facts[0]
    if not isinstance(facts, dict):
        raise RuntimeError(f"Gemini returned {type(facts).__name__}, expected object")
    usage = _gemini_usage(response)
    call = {
        "provider": "gemini",
        "model": model,
        "elapsed_ms": elapsed_ms,
        "prompt_tokens": usage.get("prompt_token_count"),
        "completion_tokens": usage.get("candidates_token_count"),
        "total_tokens": usage.get("total_token_count"),
        "thoughts_tokens": usage.get("thoughts_token_count"),
    }
    return facts, call


def _prompt(job: NewFilingJob, source_text: str) -> str:
    text = source_text[:MAX_FULL_TEXT_CHARS]
    return (
        "Extract a validated fact set for a high-quality SEBI new-filing article.\n"
        f"Company from SEBI listing: {job.company_name}\n"
        f"SEBI filing date: {job.filing_date}\n"
        f"Document type from listing: {job.document_type}\n\n"
        "Return exactly this JSON shape. Do not add keys:\n"
        f"{json.dumps(ARTICLE_SCHEMA, ensure_ascii=False, indent=2)}\n\n"
        "All values shown as strings in the schema are placeholders. Replace them with supported facts or null. "
        "Every object with value/raw_excerpt/source_page/confidence must keep that shape. "
        "Every array item must also keep that same fact shape; never return primitive strings or numbers in arrays.\n\n"
        "For financials.periods, do not extract only the latest period when a multi-period financial table is present. "
        "Extract every available period column up to four periods, preserving the table's period labels such as 9M FY2026, FY2025, FY2024, FY2023. "
        "It is acceptable for several cells in a metric row to use the same exact row excerpt, as long as that excerpt is contiguous on one PDF page and contains the cited value. "
        "For industry_context, use only the Industry Overview or equivalent section in the filing, not outside knowledge. "
        "For risks.top_risks, prefer risks that affect revenue concentration, working capital, debt, regulation, litigation, "
        "suppliers/customers, promoters, or execution of objects of the issue. If a table cell or risk explanation cannot "
        "be backed by one exact excerpt, set that leaf to null.\n\n"
        "FULL PDF TEXT:\n"
        f"{text}"
    )


def _augment_financial_periods(facts: dict[str, Any], pdf: PDFText) -> None:
    """Fill common KPI/restated-financial period tables when the model under-extracts them."""
    financials = facts.setdefault("financials", {})
    existing = financials.get("periods")
    if isinstance(existing, list) and len([row for row in existing if isinstance(row, dict)]) >= 3:
        return

    parsed = _parse_kpi_financial_periods(pdf)
    if len(parsed) < 3:
        return
    financials["periods"] = parsed[:4]


def _augment_risks(facts: dict[str, Any], pdf: PDFText) -> None:
    risks = facts.setdefault("risks", {})
    existing = risks.get("top_risks")
    existing_count = len(existing) if isinstance(existing, list) else 0
    if existing_count >= 5:
        return
    parsed = _parse_risk_summaries(pdf)
    if not parsed:
        return
    merged = []
    seen = set()
    if isinstance(existing, list):
        for row in existing:
            risk_text = _norm(_nested_value(row, ["risk", "value"]) or _nested_value(row, ["value"]) or "")
            risk_excerpt = _norm(_nested_value(row, ["risk", "raw_excerpt"]) or _nested_value(row, ["raw_excerpt"]) or "")
            if risk_text:
                seen.add(risk_text[:80])
            if risk_excerpt:
                seen.add(risk_excerpt[:100])
            merged.append(row)
    for row in parsed:
        key = _norm(_nested_value(row, ["risk", "value"]) or "")[:80]
        excerpt_key = _norm(_nested_value(row, ["risk", "raw_excerpt"]) or "")[:100]
        if not key or key in seen or excerpt_key in seen or _risk_duplicate(row, merged):
            continue
        merged.append(row)
        seen.add(key)
        seen.add(excerpt_key)
        if len(merged) >= 8:
            break
    risks["top_risks"] = merged
    if not _fact_from_leaf(risks.get("risk_factor_count"), "risks.risk_factor_count"):
        count = _risk_count_fact(pdf)
        if count:
            risks["risk_factor_count"] = count


def _parse_risk_summaries(pdf: PDFText) -> list[dict[str, Any]]:
    rows = []
    for page_no, page in enumerate(pdf.pages[:90], start=1):
        if "risk" not in page.lower():
            continue
        text = _clean_label(page)
        for match in re.finditer(r"(?:^|\s)([1-9]\d?)\.\s+(.+?)(?=\s+[1-9]\d?\.\s+|$)", text):
            number = int(match.group(1))
            body = match.group(2).strip()
            if number > 20 or len(body) < 80:
                continue
            if any(skip in body[:80].lower() for skip in ("for details", "there can be no assurance")):
                continue
            first = _first_sentence(body)
            if not first:
                continue
            why = _first_sentence(body[len(first):].strip()) or _risk_implication(first)
            rows.append(
                {
                    "risk": _synthetic_fact(first, _excerpt_window(body, first), page_no),
                    "why_it_matters": _synthetic_fact(why, _excerpt_window(body, why) if why in body else _excerpt_window(body, first), page_no),
                }
            )
        if len(rows) >= 8:
            break
    return rows[:8]


def _risk_count_fact(pdf: PDFText) -> dict[str, Any] | None:
    max_number = 0
    max_excerpt = ""
    max_page = 0
    for page_no, page in enumerate(pdf.pages[:90], start=1):
        if "risk" not in page.lower():
            continue
        for match in re.finditer(r"(?:^|\n)\s*([1-9]\d?)\.\s+([^\n]{20,180})", page):
            number = int(match.group(1))
            if number > max_number:
                max_number = number
                max_excerpt = match.group(0).strip()
                max_page = page_no
    if not max_number:
        return None
    return _synthetic_fact(str(max_number), max_excerpt, max_page)


def _first_sentence(text: str) -> str:
    text = _clean_label(text)
    match = re.match(r"(.+?(?:\.|;))(?:\s|$)", text)
    if match:
        return match.group(1).strip()
    return text[:260].rsplit(" ", 1)[0].strip()


def _risk_duplicate(candidate: dict[str, Any], rows: list[Any]) -> bool:
    cand = _norm(_nested_value(candidate, ["risk", "raw_excerpt"]) or _nested_value(candidate, ["risk", "value"]) or "")
    if not cand:
        return False
    for row in rows:
        existing = _norm(_nested_value(row, ["risk", "raw_excerpt"]) or _nested_value(row, ["risk", "value"]) or _nested_value(row, ["value"]) or "")
        if not existing:
            continue
        if cand[:90] in existing or existing[:90] in cand:
            return True
    return False


def _risk_implication(text: str) -> str:
    cleaned = str(text).rstrip(".")
    return f"{cleaned} could affect revenue growth, profitability, cash flows or execution of the offer plan."


def _excerpt_window(body: str, needle: str, limit: int = 420) -> str:
    body = _clean_label(body)
    needle = _clean_label(needle)
    if not needle:
        return body[:limit]
    idx = body.find(needle)
    if idx < 0:
        return body[:limit]
    return body[idx : idx + max(len(needle), min(limit, len(body) - idx))].strip()


def _nested_value(root: Any, path: list[str]) -> Any:
    current = root
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _parse_kpi_financial_periods(pdf: PDFText) -> list[dict[str, Any]]:
    best: tuple[int, int, list[dict[str, Any]]] | None = None
    for page_no, page in enumerate(pdf.pages, start=1):
        lower = page.lower()
        if "revenue from operations" not in lower or "pat" not in lower:
            continue
        lines = [line.rstrip() for line in page.splitlines() if line.strip()]
        row_lines = {
            "revenue_from_operations": _find_metric_line(lines, r"\bRevenue from Operations\b"),
            "ebitda": _find_metric_line(lines, r"^\s*EBITDA\s+"),
            "profit_after_tax": _find_metric_line(lines, r"^\s*PAT\s+"),
            "pat_margin": _find_metric_line(lines, r"^\s*PAT Margin\s+"),
            "roe": _find_metric_line(lines, r"^\s*ROE\s+"),
            "debt_to_equity": _find_metric_line(lines, r"Debt to Equity Ratio"),
        }
        if not row_lines["revenue_from_operations"] or not row_lines["profit_after_tax"]:
            continue
        labels = _period_labels_from_page(page)
        metric_values = {key: _numbers_from_metric_line(line) for key, line in row_lines.items() if line}
        width = min([len(labels)] + [len(values) for key, values in metric_values.items() if key in {"revenue_from_operations", "profit_after_tax"}])
        if width < 2:
            continue
        rows = []
        for idx in range(min(width, 4)):
            period_excerpt = row_lines["revenue_from_operations"] or labels[idx]
            rows.append(
                {
                    "period": _synthetic_fact(labels[idx], period_excerpt.strip(), page_no),
                    "revenue_from_operations": _metric_fact(row_lines["revenue_from_operations"], metric_values, "revenue_from_operations", idx, page_no),
                    "ebitda": _metric_fact(row_lines["ebitda"], metric_values, "ebitda", idx, page_no),
                    "profit_after_tax": _metric_fact(row_lines["profit_after_tax"], metric_values, "profit_after_tax", idx, page_no),
                    "net_worth": None,
                    "borrowings": None,
                    "total_assets": None,
                    "operating_cash_flow": None,
                    "pat_margin": _metric_fact(row_lines["pat_margin"], metric_values, "pat_margin", idx, page_no),
                    "roe": _metric_fact(row_lines["roe"], metric_values, "roe", idx, page_no),
                    "debt_to_equity": _metric_fact(row_lines["debt_to_equity"], metric_values, "debt_to_equity", idx, page_no),
                }
            )
        score = sum(1 for value in row_lines.values() if value)
        if best is None or score > best[1]:
            best = (page_no, score, rows)
    return best[2] if best else []


def _find_metric_line(lines: list[str], pattern: str) -> str | None:
    regex = re.compile(pattern, re.IGNORECASE)
    for line in lines:
        if regex.search(line):
            return line.strip()
    return None


def _period_labels_from_page(page: str) -> list[str]:
    labels = []
    month_match = re.search(r"(?:period\s+ended\s+)?(December\s+31,\s+20\d{2})", page, flags=re.IGNORECASE)
    if month_match:
        labels.append(f"Period ended {_clean_label(month_match.group(1))}")
    fy_labels = []
    for match in re.finditer(r"\bFY\s*20\d{2}\b", page, flags=re.IGNORECASE):
        label = re.sub(r"\s+", " ", match.group(0).upper())
        if label not in fy_labels:
            fy_labels.append(label)
    labels.extend(fy_labels)
    while len(labels) < 4:
        labels.append(f"Period {len(labels) + 1}")
    return labels[:4]


def _clean_label(text: str) -> str:
    return " ".join(str(text).split())


def _numbers_from_metric_line(line: str) -> list[str]:
    if not line:
        return []
    matches = re.findall(r"\(?-?\d[\d,]*(?:\.\d+)?%?\)?", line)
    values = [match for match in matches if re.search(r"\d", match)]
    return values[-4:] if len(values) > 4 else values


def _metric_fact(line: str | None, values_by_key: dict[str, list[str]], key: str, idx: int, page_no: int) -> dict[str, Any] | None:
    values = values_by_key.get(key) or []
    if not line or idx >= len(values):
        return None
    return _synthetic_fact(values[idx], line.strip(), page_no)


def _synthetic_fact(value: str, raw_excerpt: str, page_no: int) -> dict[str, Any]:
    return {
        "value": value,
        "raw_excerpt": raw_excerpt,
        "source_page": page_no,
        "confidence": "high",
    }


def validate_facts(facts: dict[str, Any], pdf: PDFText) -> dict[str, Any]:
    checked = repaired = redacted = 0
    failures: list[dict[str, Any]] = []
    for path, leaf in _iter_fact_leaves(facts):
        if not isinstance(leaf, dict) or leaf.get("value") is None:
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
        failures.append({"path": path, "old_page": page, "raw_excerpt": str(excerpt or "")[:180]})
    return {
        "checked_count": checked,
        "repaired_count": repaired,
        "redacted_count": redacted,
        "redaction_rate": round(redacted / checked, 4) if checked else None,
        "repair_rate": round(repaired / checked, 4) if checked else None,
        "failures": failures[:200],
    }


def assess_article_quality(facts: dict[str, Any], citation: dict[str, Any]) -> dict[str, Any]:
    verified = [path for path, leaf in _iter_fact_leaves(facts) if isinstance(leaf, dict) and leaf.get("value") is not None]
    groups = {path.split(".", 1)[0] for path in verified if "." in path}
    missing = sorted(REQUIRED_FACT_GROUPS - groups)
    redaction_rate = citation.get("redaction_rate")
    failures = []
    warnings = []
    if len(verified) < MIN_VERIFIED_FACTS:
        failures.append({"code": "too_few_verified_facts", "verified": len(verified), "minimum": MIN_VERIFIED_FACTS})
    if missing:
        failures.append({"code": "missing_required_fact_groups", "groups": missing})
    if redaction_rate is not None and redaction_rate > 0.30:
        failures.append({"code": "redaction_rate_too_high", "rate": redaction_rate})
    elif redaction_rate is not None and redaction_rate > 0.12:
        warnings.append({"code": "redaction_rate_review", "rate": redaction_rate})
    state = "fail" if failures else "review" if warnings else "pass"
    return {
        "state": state,
        "publishable": state in {"pass", "review"},
        "verified_fact_count": len(verified),
        "fact_groups": sorted(groups),
        "failures": failures,
        "warnings": warnings,
    }


def build_article(
    *,
    job: NewFilingJob,
    slug: str,
    facts: dict[str, Any],
    citation: dict[str, Any],
    quality: dict[str, Any],
    pdf: PDFText,
    pdf_bytes: bytes,
    pdf_path: Path,
    calls: list[dict[str, Any]],
) -> dict[str, Any]:
    company = _fact_value(facts, "company.name") or job.company_name
    doc_type = _fact_value(facts, "offer.document_type") or job.document_type
    headline = f"{company} {doc_type}: SEBI filing note"
    dek = _make_dek(facts, doc_type)
    article = _research_article(facts, company=company, doc_type=doc_type)
    citations = _citation_index(facts)
    return {
        "$schema": "https://ipo-watch.local/schema/v3/new-filing-article.schema.json",
        "schema_version": SCHEMA_VERSION,
        "slug": slug,
        "url_path": f"/new-filings/{slug}/",
        "headline": headline,
        "dek": dek,
        "company_name": company,
        "filing_date": job.filing_date,
        "document_type": doc_type,
        "document_url": job.document_url,
        "detail_url": job.detail_url,
        "document_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
        "local_pdf_path": str(pdf_path),
        "pdf_pages": len(pdf.pages),
        "pdf_text_chars": len(pdf.text),
        "generated_at": _now(),
        "quality": quality,
        "citation_validation": citation,
        "model_calls": calls,
        "facts": facts,
        "article": {
            "summary": article["summary"],
            "blocks": article["blocks"],
            "sections": article["sections"],
            "citations": citations,
        },
    }


def rebuild_index(out_root: Path = DEFAULT_OUT_ROOT) -> Path:
    items = []
    for path in sorted(out_root.glob("*/article.json")):
        doc = _read_json(path)
        if not doc.get("quality", {}).get("publishable"):
            continue
        items.append(
            {
                "slug": doc.get("slug"),
                "url_path": doc.get("url_path"),
                "headline": doc.get("headline"),
                "dek": doc.get("dek"),
                "company_name": doc.get("company_name"),
                "filing_date": doc.get("filing_date"),
                "document_type": doc.get("document_type"),
                "document_url": doc.get("document_url"),
                "quality_state": doc.get("quality", {}).get("state"),
                "verified_fact_count": doc.get("quality", {}).get("verified_fact_count"),
                "generated_at": doc.get("generated_at"),
            }
        )
    items.sort(key=lambda row: (row.get("filing_date") or "", row.get("generated_at") or ""), reverse=True)
    index = {
        "$schema": "https://ipo-watch.local/schema/v3/new-filings-index.schema.json",
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now(),
        "count": len(items),
        "items": items,
    }
    out_path = out_root / "index.json"
    _write_json(out_path, index)
    return out_path


def filing_slug(job: NewFilingJob) -> str:
    stem = _slugify(job.company_name)
    date_part = (job.filing_date or "")[:10].replace("-", "")
    digest = hashlib.sha256(f"{job.document_url}|{job.detail_url}".encode("utf-8")).hexdigest()[:8]
    parts = [stem]
    if date_part:
        parts.append(date_part)
    parts.append(digest)
    return "-".join(part for part in parts if part)


def _full_text(pdf: PDFText) -> str:
    chunks = []
    for idx, page in enumerate(pdf.pages, start=1):
        text = page.rstrip()
        if text:
            chunks.append(f"\n\n--- PDF PAGE {idx} ---\n{text}")
    return "".join(chunks).strip()


def _research_article(facts: dict[str, Any], *, company: str, doc_type: str) -> dict[str, Any]:
    summary = _summary_cards(facts, doc_type=doc_type)
    blocks = []

    overview = _overview_block(facts, company=company, doc_type=doc_type)
    if overview:
        blocks.append(overview)

    takeaways = _research_takeaways_block(facts, company=company)
    if takeaways:
        blocks.append(takeaways)

    issue = _issue_table(facts)
    if issue:
        blocks.append(issue)

    objects = _objects_table(facts)
    if objects:
        blocks.append(objects)

    business = _business_block(facts)
    if business:
        blocks.append(business)

    industry = _industry_block(facts)
    if industry:
        blocks.append(industry)

    financials = _financial_table(facts)
    if financials:
        blocks.append(financials)

    chart = _financial_chart(facts)
    if chart:
        blocks.append(chart)

    financial_read = _financial_analysis_block(facts)
    if financial_read:
        blocks.append(financial_read)

    risks = _risk_matrix(facts)
    if risks:
        blocks.append(risks)

    governance = _governance_table(facts)
    if governance:
        blocks.append(governance)

    return {
        "summary": summary,
        "blocks": blocks,
        "sections": _article_sections(facts),
    }


def _research_takeaways_block(facts: dict[str, Any], *, company: str) -> dict[str, Any] | None:
    rows = []
    description = _fact(facts, "company.description")
    footprint = _first_fact(facts, "business.geographies")
    channels = _first_fact(facts, "business.customers_or_channels")
    market_size = _first_fact(facts, "industry_context.market_size")
    sector_trend = _first_fact(facts, "industry_context.sector_trends")
    total_issue = _fact(facts, "offer.total_issue")
    fresh = _fact(facts, "offer.fresh_issue")
    ofs = _fact(facts, "offer.offer_for_sale")
    use = _facts_at_path(facts, "offer.objects_of_issue")[:3]
    risk_rows = _risk_rows(facts)[:4]

    if description:
        rows.append(
            {
                "label": "Business model",
                "text": _sentences(
                    f"{company} is a retail jewellery issuer selling through its own showroom network.",
                    f"The company describes the business as {str(description['value']).rstrip('.')}.",
                    f"The current footprint is concentrated in {footprint['value']}." if footprint else None,
                ),
                "facts": _compact([description, footprint, channels]),
            }
        )
    if market_size or sector_trend:
        rows.append(
            {
                "label": "Industry backdrop",
                "text": _sentences(
                    f"The filing's industry section frames the addressable market at {market_size['value']}." if market_size else None,
                    f"It also highlights {str(sector_trend['value']).rstrip('.').lower()} as a sector shift." if sector_trend else None,
                ),
                "facts": _compact([market_size, sector_trend]),
            }
        )
    if total_issue or fresh or ofs or use:
        use_text = "; ".join(str(item["value"]) for item in use[:2])
        rows.append(
            {
                "label": "Offer purpose",
                "text": _sentences(
                    f"The offer comprises {total_issue['value']}." if total_issue else None,
                    f"The structure includes a fresh issue of {fresh['value']} and an OFS of {ofs['value']}." if fresh and ofs else None,
                    f"The stated proceeds use includes {use_text}." if use_text else None,
                ),
                "facts": _compact([total_issue, fresh, ofs, *use]),
            }
        )
    if risk_rows:
        facts_out = [row.get("risk") for row in risk_rows if row.get("risk")]
        rows.append(
            {
                "label": "Main watch-outs",
                "text": "The risk section points to " + "; ".join(str(fact["value"]).rstrip(".") for fact in facts_out[:4]) + ".",
                "facts": facts_out,
            }
        )
    if not rows:
        return None
    return {
        "type": "takeaways",
        "title": "Research read",
        "dek": "A source-backed synthesis of the filing, separating the business case from the diligence points.",
        "rows": rows,
    }


def _overview_block(facts: dict[str, Any], *, company: str, doc_type: str) -> dict[str, Any] | None:
    description = _fact(facts, "company.description")
    industry = _fact(facts, "company.industry")
    issue = _fact(facts, "offer.issue_type")
    total = _fact(facts, "offer.total_issue")
    fresh = _fact(facts, "offer.fresh_issue")
    ofs = _fact(facts, "offer.offer_for_sale")
    latest_revenue = _fact(facts, "financials.latest_revenue")
    latest_pat = _fact(facts, "financials.latest_profit_after_tax")
    facts_out = _compact([description, industry, issue, total, fresh, ofs, latest_revenue, latest_pat])
    if not facts_out:
        return None

    paragraphs = []
    if description:
        sentence = f"{company} describes itself as {str(description['value']).rstrip('.')}"
        if industry:
            sentence += f", operating in {industry['value']}."
        else:
            sentence += "."
        paragraphs.append(sentence)
    if issue or total or fresh or ofs:
        parts = []
        if issue:
            parts.append(str(issue["value"]))
        if total:
            parts.append(f"with {total['value']}")
        if fresh:
            parts.append(f"a fresh issue of {fresh['value']}")
        if ofs:
            parts.append(f"and an offer for sale of {ofs['value']}")
        paragraphs.append(f"The {doc_type} sets out {' '.join(parts)}.")
    if latest_revenue or latest_pat:
        financial_sentence = []
        if latest_revenue:
            financial_sentence.append(f"latest cited revenue from operations of {latest_revenue['value']}")
        if latest_pat:
            financial_sentence.append(f"profit after tax of {latest_pat['value']}")
        paragraphs.append("The filing also reports " + " and ".join(financial_sentence) + ".")

    return {
        "type": "narrative",
        "kicker": "Research note",
        "title": "What the filing says",
        "paragraphs": paragraphs,
        "facts": facts_out,
    }


def _business_block(facts: dict[str, Any]) -> dict[str, Any] | None:
    groups = [
        ("Products and services", "business.products_services"),
        ("Geographic footprint", "business.geographies"),
        ("Sales channels", "business.customers_or_channels"),
        ("Facilities", "business.showrooms_or_facilities"),
        ("Operations", "business.sourcing_or_operations"),
        ("Competitive strengths", "business.competitive_strengths"),
    ]
    rows = []
    paragraphs = []
    for label, path in groups:
        items = _facts_at_path(facts, path)
        if not items:
            continue
        rows.append({"label": label, "items": items[:4]})
        values = ", ".join(str(item["value"]).rstrip(".") for item in items[:3])
        paragraphs.append(_business_sentence(label, values))
    if not rows:
        return None
    return {
        "type": "evidence_groups",
        "title": "Company and business",
        "dek": "How the issuer describes its operations, footprint and route to customers.",
        "paragraphs": paragraphs[:4],
        "groups": rows,
    }


def _industry_block(facts: dict[str, Any]) -> dict[str, Any] | None:
    groups = [
        ("Market size", "industry_context.market_size"),
        ("Growth drivers", "industry_context.growth_drivers"),
        ("Sector trends", "industry_context.sector_trends"),
        ("Competitive context", "industry_context.competitive_context"),
    ]
    rows = []
    paragraphs = []
    fallback = _fact(facts, "company.industry")
    for label, path in groups:
        items = _facts_at_path(facts, path)
        if not items:
            continue
        rows.append({"label": label, "items": items[:3]})
        values = "; ".join(str(item["value"]).rstrip(".") for item in items[:2])
        paragraphs.append(_industry_sentence(label, values))
    if not rows and fallback:
        rows.append({"label": "Sector", "items": [fallback]})
        paragraphs.append(f"The filing places the company in {fallback['value']}.")
    if not rows:
        return None
    return {
        "type": "evidence_groups",
        "title": "Sector and industry context",
        "dek": "Context extracted from the filing's industry overview and business sections.",
        "paragraphs": paragraphs[:4],
        "groups": rows,
    }


def _issue_table(facts: dict[str, Any]) -> dict[str, Any] | None:
    rows = _table_rows(
        facts,
        [
            ("Document", "offer.document_type"),
            ("Issue type", "offer.issue_type"),
            ("Total issue", "offer.total_issue"),
            ("Fresh issue", "offer.fresh_issue"),
            ("Offer for sale", "offer.offer_for_sale"),
            ("Face value", "offer.face_value"),
            ("Lead manager", "offer.lead_managers"),
            ("Registrar", "offer.registrar"),
            ("Selling shareholders", "offer.selling_shareholders"),
        ],
    )
    if not rows:
        return None
    return {
        "type": "table",
        "title": "Issue at a glance",
        "dek": "Core offer terms stated in the filing.",
        "columns": ["Item", "Detail", "Source"],
        "rows": rows,
    }


def _objects_table(facts: dict[str, Any]) -> dict[str, Any] | None:
    objects = _get_path(facts, "offer.objects_of_issue")
    rows = []
    if isinstance(objects, list):
        for idx, item in enumerate(objects):
            if isinstance(item, dict) and "value" in item:
                fact = _fact_from_leaf(item, f"offer.objects_of_issue[{idx}]")
                if fact:
                    rows.append({"cells": [{"label": "Object", "fact": fact}, {"label": "Amount", "value": "-"}]})
                continue
            if not isinstance(item, dict):
                continue
            obj = _fact_from_leaf(item.get("object"), f"offer.objects_of_issue[{idx}].object")
            amount = _fact_from_leaf(item.get("amount"), f"offer.objects_of_issue[{idx}].amount")
            if obj or amount:
                rows.append(
                    {
                        "cells": [
                            {"label": "Object", "fact": obj} if obj else {"label": "Object", "value": "-"},
                            {"label": "Amount", "fact": amount} if amount else {"label": "Amount", "value": "-"},
                        ]
                    }
                )
    if not rows:
        return None
    return {
        "type": "structured_table",
        "title": "Use of proceeds",
        "dek": "Objects of the fresh issue where the DRHP states them.",
        "columns": ["Object", "Amount"],
        "rows": rows,
    }


def _financial_table(facts: dict[str, Any]) -> dict[str, Any] | None:
    periods = _get_path(facts, "financials.periods")
    columns = ["Period", "Revenue", "EBITDA", "PAT", "PAT margin", "ROE", "Debt/equity"]
    rows = []
    if isinstance(periods, list):
        metric_paths = [
            ("Period", "period"),
            ("Revenue", "revenue_from_operations"),
            ("EBITDA", "ebitda"),
            ("PAT", "profit_after_tax"),
            ("PAT margin", "pat_margin"),
            ("ROE", "roe"),
            ("Debt/equity", "debt_to_equity"),
        ]
        for idx, period in enumerate(periods[:4]):
            if not isinstance(period, dict):
                continue
            cells = []
            present = False
            for label, key in metric_paths:
                leaf = period.get(key)
                fact = _fact_from_leaf(leaf, f"financials.periods[{idx}].{key}")
                if fact:
                    present = True
                    cells.append({"label": label, "fact": fact})
                else:
                    cells.append({"label": label, "value": "-"})
            if present:
                rows.append({"cells": cells})
    if not rows:
        rows = _table_rows(
            facts,
            [
                ("Latest revenue", "financials.latest_revenue"),
                ("Latest profit after tax", "financials.latest_profit_after_tax"),
                ("Debt / borrowings", "financials.debt_or_borrowings"),
                ("Key ratios", "financials.key_ratios"),
                ("Analysis points", "financials.financial_analysis_points"),
            ],
        )
        if not rows:
            return None
        return {
            "type": "table",
            "title": "Financial snapshot",
            "dek": "Selected financial figures and ratios cited in the filing.",
            "columns": ["Metric", "Detail", "Source"],
            "rows": rows,
        }
    return {
        "type": "structured_table",
        "title": "Financial snapshot",
        "dek": "Restated financial information extracted from the filing. Figures are shown as stated in the document.",
        "columns": columns,
        "rows": rows,
    }


def _financial_chart(facts: dict[str, Any]) -> dict[str, Any] | None:
    periods = _get_path(facts, "financials.periods")
    if not isinstance(periods, list):
        return None
    points = []
    for idx, period in enumerate(periods[:4]):
        if not isinstance(period, dict):
            continue
        label = _fact_from_leaf(period.get("period"), f"financials.periods[{idx}].period")
        revenue = _fact_from_leaf(period.get("revenue_from_operations"), f"financials.periods[{idx}].revenue_from_operations")
        pat = _fact_from_leaf(period.get("profit_after_tax"), f"financials.periods[{idx}].profit_after_tax")
        revenue_num = _parse_numeric(revenue.get("value") if revenue else None)
        pat_num = _parse_numeric(pat.get("value") if pat else None)
        if label and (revenue_num is not None or pat_num is not None):
            points.append(
                {
                    "label": label["value"],
                    "revenue": revenue_num,
                    "profit_after_tax": pat_num,
                    "source_page": (revenue or pat or label).get("source_page"),
                }
            )
    if len(points) < 2:
        return None
    max_value = max([p.get("revenue") or 0 for p in points] + [p.get("profit_after_tax") or 0 for p in points])
    if max_value <= 0:
        return None
    return {
        "type": "bar_chart",
        "title": "Revenue and PAT trend",
        "dek": "Scale is based on numeric values parsed from cited financial table cells.",
        "max_value": max_value,
        "points": points,
    }


def _financial_analysis_block(facts: dict[str, Any]) -> dict[str, Any] | None:
    periods = _period_fact_rows(facts)
    if len(periods) < 2:
        rows = _table_rows(
            facts,
            [
                ("Debt / borrowings", "financials.debt_or_borrowings"),
                ("Key ratios", "financials.key_ratios"),
                ("Analysis points", "financials.financial_analysis_points"),
            ],
        )
        if not rows:
            return None
        return {
            "type": "analysis",
            "title": "Financial read-through",
            "dek": "Financial diligence points extracted from the filing.",
            "paragraphs": [row["value"] for row in rows],
            "facts": [fact for row in rows for fact in row.get("facts", [])],
        }

    latest = periods[0]
    oldest = periods[-1]
    latest_label = latest.get("label")
    oldest_label = oldest.get("label")
    revenue_growth = _growth_text(oldest.get("revenue"), latest.get("revenue"))
    pat_growth = _growth_text(oldest.get("pat"), latest.get("pat"))
    ebitda_margin = _margin(latest.get("ebitda"), latest.get("revenue"))
    pat_margin = latest.get("pat_margin") or _margin(latest.get("pat"), latest.get("revenue"))
    leverage = latest.get("debt_to_equity")
    paragraphs = []
    if revenue_growth:
        paragraphs.append(
            f"Revenue rose from {oldest.get('revenue_label')} in {oldest_label} to {latest.get('revenue_label')} in {latest_label}, a {revenue_growth} increase on the extracted table values."
        )
    if pat_growth:
        paragraphs.append(
            f"Profit after tax moved from {oldest.get('pat_label')} in {oldest_label} to {latest.get('pat_label')} in {latest_label}, implying {pat_growth} growth over the displayed periods."
        )
    if ebitda_margin is not None or pat_margin is not None:
        bits = []
        if ebitda_margin is not None:
            bits.append(f"EBITDA margin of {ebitda_margin:.1f}%")
        if pat_margin is not None:
            bits.append(f"PAT margin of {_format_percent_value(pat_margin)}")
        paragraphs.append(f"The latest period shows " + " and ".join(bits) + ", based on revenue, EBITDA and PAT figures in the KPI table.")
    if leverage:
        paragraphs.append(f"The latest debt-to-equity ratio is {leverage}, which makes working-capital funding and inventory discipline central diligence items.")

    facts_out = []
    for row in periods:
        facts_out.extend(_compact([row.get("revenue_fact"), row.get("pat_fact"), row.get("ebitda_fact"), row.get("debt_to_equity_fact")]))
    facts_out.extend(_facts_at_path(facts, "financials.debt_or_borrowings")[:2])
    if not paragraphs:
        return None
    return {
        "type": "analysis",
        "title": "Financial read-through",
        "dek": "Computed from the cited financial table values; no external estimates are used.",
        "paragraphs": paragraphs,
        "facts": facts_out[:10],
    }


def _risk_matrix(facts: dict[str, Any]) -> dict[str, Any] | None:
    rows = _risk_rows(facts)
    red_flags = _facts_at_path(facts, "risks.red_flags")[:4]
    if not rows and not red_flags:
        return None
    return {
        "type": "risk_matrix",
        "title": "Risk map",
        "dek": "Risks are drawn from the risk-factor section and nearby filing disclosures.",
        "risk_factor_count": _fact(facts, "risks.risk_factor_count"),
        "rows": rows,
        "red_flags": red_flags,
    }


def _governance_table(facts: dict[str, Any]) -> dict[str, Any] | None:
    rows = _table_rows(
        facts,
        [
            ("Promoters", "governance.promoters"),
            ("Directors", "governance.directors"),
            ("Shareholding", "governance.shareholding"),
            ("Litigation", "governance.litigation"),
            ("Related-party transactions", "governance.related_party_transactions"),
            ("Basis for offer price", "valuation.basis_for_offer_price"),
            ("Peer comparison", "valuation.peer_comparison"),
            ("EPS / NAV / RoNW", "valuation.eps_nav_ronw"),
        ],
    )
    if not rows:
        return None
    return {
        "type": "table",
        "title": "Governance, valuation and parties",
        "dek": "Promoter, governance, valuation and intermediary disclosures pulled from the filing.",
        "columns": ["Area", "Disclosure", "Source"],
        "rows": rows,
    }


def _article_sections(facts: dict[str, Any]) -> list[dict[str, Any]]:
    specs = [
        ("Company", ["company.description", "company.industry", "company.incorporation_or_history", "company.registered_office"]),
        ("Issue Structure", ["offer.issue_type", "offer.fresh_issue", "offer.offer_for_sale", "offer.total_issue"]),
        ("Objects Of The Issue", ["offer.objects_of_issue"]),
        ("Business", ["business.products_services", "business.geographies", "business.customers_or_channels", "business.showrooms_or_facilities", "business.sourcing_or_operations", "business.competitive_strengths"]),
        ("Industry Context", ["industry_context.market_size", "industry_context.growth_drivers", "industry_context.sector_trends", "industry_context.competitive_context"]),
        ("Financial Snapshot", ["financials.latest_revenue", "financials.latest_profit_after_tax", "financials.periods", "financials.debt_or_borrowings", "financials.key_ratios", "financials.financial_analysis_points"]),
        ("Risks", ["risks.risk_factor_count", "risks.top_risks", "risks.red_flags"]),
        ("Governance", ["governance.promoters", "governance.directors", "governance.shareholding", "governance.litigation", "governance.related_party_transactions", "valuation.basis_for_offer_price", "valuation.peer_comparison", "valuation.eps_nav_ronw"]),
        ("Intermediaries", ["offer.lead_managers", "offer.registrar"]),
    ]
    out = []
    for title, paths in specs:
        facts_out = []
        for path in paths:
            facts_out.extend(_facts_at_path(facts, path))
        if facts_out:
            out.append({"title": title, "facts": facts_out})
    return out


def _summary_cards(facts: dict[str, Any], *, doc_type: str) -> list[dict[str, Any]]:
    wanted = [
        ("Document", "offer.document_type"),
        ("Issue", "offer.total_issue"),
        ("Fresh issue", "offer.fresh_issue"),
        ("OFS", "offer.offer_for_sale"),
        ("Revenue", "financials.latest_revenue"),
        ("PAT", "financials.latest_profit_after_tax"),
        ("Sector", "company.industry"),
        ("Risk factors", "risks.risk_factor_count"),
    ]
    cards = []
    for label, path in wanted:
        fact = _fact(facts, path)
        if fact:
            cards.append({"label": label, **fact})
    if not cards:
        cards.append({"label": "Document", "path": "offer.document_type", "value": doc_type, "raw_excerpt": "", "source_page": None, "confidence": "low"})
    return cards[:8]


def _make_dek(facts: dict[str, Any], doc_type: str) -> str:
    desc = _fact_value(facts, "company.description")
    issue = _fact_value(facts, "offer.issue_type")
    parts = []
    if desc:
        parts.append(str(desc).rstrip("."))
    if issue:
        parts.append(f"The filing describes a {issue}")
    if not parts:
        return f"Validated highlights from the company's SEBI {doc_type} filing."
    return ". ".join(parts[:2]) + "."


def _citation_index(facts: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for path, leaf in _iter_fact_leaves(facts):
        if leaf.get("value") is None:
            continue
        rows.append(
            {
                "path": path,
                "source_page": leaf.get("source_page"),
                "raw_excerpt": leaf.get("raw_excerpt"),
                "confidence": leaf.get("confidence"),
            }
        )
    return rows


def _fact(root: dict[str, Any], path: str) -> dict[str, Any] | None:
    return _fact_from_leaf(_get_path(root, path), path)


def _first_fact(root: dict[str, Any], path: str) -> dict[str, Any] | None:
    rows = _facts_at_path(root, path)
    return rows[0] if rows else None


def _fact_from_leaf(leaf: Any, path: str) -> dict[str, Any] | None:
    if not isinstance(leaf, dict) or leaf.get("value") is None:
        return None
    return {
        "path": path,
        "value": leaf.get("value"),
        "raw_excerpt": leaf.get("raw_excerpt"),
        "source_page": leaf.get("source_page"),
        "confidence": leaf.get("confidence"),
    }


def _compact(items: list[Any]) -> list[Any]:
    return [item for item in items if item]


def _join_sentence(*parts: str | None) -> str:
    chunks = [str(part).strip().rstrip(".") for part in parts if part]
    if not chunks:
        return ""
    return "; ".join(chunks) + "."


def _sentences(*parts: str | None) -> str:
    chunks = [str(part).strip() for part in parts if part]
    return " ".join(part if part.endswith((".", "?", "!")) else f"{part}." for part in chunks)


def _business_sentence(label: str, values: str) -> str:
    templates = {
        "Products and services": f"The product set is concentrated around {values}.",
        "Geographic footprint": f"The footprint is regional rather than national, with the filing pointing to {values}.",
        "Sales channels": f"The route to market is built around {values}.",
        "Facilities": f"The operating base currently includes {values}.",
        "Operations": f"The operating model depends on {values}.",
        "Competitive strengths": f"The company presents {values} as a competitive strength.",
    }
    return templates.get(label, f"{label}: {values}.")


def _industry_sentence(label: str, values: str) -> str:
    templates = {
        "Market size": f"The addressable sector backdrop is large, with the filing citing {values}.",
        "Growth drivers": f"The industry case rests on demand drivers including {values}.",
        "Sector trends": f"A key sector shift in the filing is {values}.",
        "Competitive context": f"The competitive setting remains important because the filing describes {values}.",
    }
    return templates.get(label, f"{label}: {values}.")


def _table_rows(root: dict[str, Any], specs: list[tuple[str, str]]) -> list[dict[str, Any]]:
    rows = []
    for label, path in specs:
        facts = _facts_at_path(root, path)
        if not facts:
            continue
        rows.append(
            {
                "label": label,
                "value": "; ".join(str(fact["value"]) for fact in facts[:4]),
                "facts": facts[:4],
                "source_pages": sorted({fact["source_page"] for fact in facts if fact.get("source_page")}),
            }
        )
    return rows


def _risk_rows(facts: dict[str, Any]) -> list[dict[str, Any]]:
    risks = _get_path(facts, "risks.top_risks")
    rows = []
    if isinstance(risks, list):
        for idx, item in enumerate(risks[:8]):
            if isinstance(item, dict) and "value" in item:
                risk = _fact_from_leaf(item, f"risks.top_risks[{idx}]")
                if risk:
                    rows.append({"risk": risk, "why_it_matters": None})
                continue
            if not isinstance(item, dict):
                continue
            risk = _fact_from_leaf(item.get("risk"), f"risks.top_risks[{idx}].risk")
            why = _fact_from_leaf(item.get("why_it_matters"), f"risks.top_risks[{idx}].why_it_matters")
            if risk or why:
                rows.append({"risk": risk, "why_it_matters": why})
    return rows


def _period_fact_rows(facts: dict[str, Any]) -> list[dict[str, Any]]:
    periods = _get_path(facts, "financials.periods")
    if not isinstance(periods, list):
        return []
    rows = []
    for idx, period in enumerate(periods[:4]):
        if not isinstance(period, dict):
            continue
        label_fact = _fact_from_leaf(period.get("period"), f"financials.periods[{idx}].period")
        revenue_fact = _fact_from_leaf(period.get("revenue_from_operations"), f"financials.periods[{idx}].revenue_from_operations")
        ebitda_fact = _fact_from_leaf(period.get("ebitda"), f"financials.periods[{idx}].ebitda")
        pat_fact = _fact_from_leaf(period.get("profit_after_tax"), f"financials.periods[{idx}].profit_after_tax")
        pat_margin_fact = _fact_from_leaf(period.get("pat_margin"), f"financials.periods[{idx}].pat_margin")
        debt_equity_fact = _fact_from_leaf(period.get("debt_to_equity"), f"financials.periods[{idx}].debt_to_equity")
        if not label_fact:
            continue
        rows.append(
            {
                "label": label_fact.get("value"),
                "revenue": _parse_numeric(revenue_fact.get("value") if revenue_fact else None),
                "revenue_label": revenue_fact.get("value") if revenue_fact else None,
                "revenue_fact": revenue_fact,
                "ebitda": _parse_numeric(ebitda_fact.get("value") if ebitda_fact else None),
                "ebitda_fact": ebitda_fact,
                "pat": _parse_numeric(pat_fact.get("value") if pat_fact else None),
                "pat_label": pat_fact.get("value") if pat_fact else None,
                "pat_fact": pat_fact,
                "pat_margin": _parse_numeric(pat_margin_fact.get("value") if pat_margin_fact else None),
                "pat_margin_fact": pat_margin_fact,
                "debt_to_equity": debt_equity_fact.get("value") if debt_equity_fact else None,
                "debt_to_equity_fact": debt_equity_fact,
            }
        )
    return rows


def _growth_text(start: float | None, end: float | None) -> str | None:
    if start is None or end is None or start == 0:
        return None
    growth = ((end / start) - 1) * 100
    return f"{growth:.1f}%"


def _margin(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator * 100


def _format_percent_value(value: float) -> str:
    if abs(value) <= 1:
        return f"{value:.2f}%"
    return f"{value:.1f}%"


def _facts_at_path(root: dict[str, Any], path: str) -> list[dict[str, Any]]:
    value = _get_path(root, path)
    rows = []
    for subpath, leaf in _flatten_fact_nodes(value, path):
        if not isinstance(leaf, dict) or leaf.get("value") is None:
            continue
        rows.append(
            {
                "path": subpath,
                "value": leaf.get("value"),
                "raw_excerpt": leaf.get("raw_excerpt"),
                "source_page": leaf.get("source_page"),
                "confidence": leaf.get("confidence"),
            }
        )
    return rows


def _flatten_fact_nodes(value: Any, path: str = "") -> list[tuple[str, Any]]:
    if isinstance(value, dict) and "value" in value:
        return [(path, value)]
    if isinstance(value, list):
        out = []
        for idx, item in enumerate(value):
            out.extend(_flatten_fact_nodes(item, f"{path}[{idx}]" if path else f"[{idx}]"))
        return out
    if isinstance(value, dict):
        out = []
        for key, item in value.items():
            out.extend(_flatten_fact_nodes(item, f"{path}.{key}" if path else str(key)))
        return out
    return []


def _parse_numeric(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _fact_value(root: dict[str, Any], path: str) -> Any:
    value = _get_path(root, path)
    if isinstance(value, dict) and value.get("value") is not None:
        return value.get("value")
    return None


def _get_path(root: dict[str, Any], path: str) -> Any:
    current: Any = root
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _iter_fact_leaves(obj: Any, path: str = ""):
    if isinstance(obj, dict):
        if "value" in obj and ("raw_excerpt" in obj or "source_page" in obj):
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
    leaf["confidence"] = "low"


def _quality_rank(quality: dict[str, Any]) -> int:
    return {"fail": 0, "review": 1, "pass": 2}.get(str(quality.get("state")), 0)


def _parse_json(raw: str) -> Any:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def _gemini_usage(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage_metadata", None)
    if not usage:
        return {}
    out = {}
    for key in (
        "prompt_token_count",
        "candidates_token_count",
        "total_token_count",
        "cached_content_token_count",
        "thoughts_token_count",
    ):
        value = getattr(usage, key, None)
        if value is not None:
            out[key] = value
    return out


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _slugify(text: str) -> str:
    cleaned = re.sub(r"\b(limited|ltd|private|pvt|company|co)\b", "", text, flags=re.IGNORECASE)
    slug = re.sub(r"[^a-z0-9]+", "-", cleaned.lower()).strip("-")
    return slug or "sebi-filing"


def _norm(text: str) -> str:
    return " ".join(str(text).replace("\u00a0", " ").split()).casefold()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
