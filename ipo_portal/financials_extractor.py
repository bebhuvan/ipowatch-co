"""Extract the full restated financial statements from a primary filing.

The 7-section ``filing_processor`` captures *scattered summary facts*
(a revenue line here, a PAT line there). This module captures the
**complete restated statements** — Profit & Loss, Balance Sheet, Cash
Flow, and the accounting-ratios table — as structured, multi-period
tables, one row per printed line item, each row carrying the exact
printed text and page so it can be citation-validated the same way.

Output: ``data/ipo_watch_v3/issues/<slug>/financials.json``.

Reuses ``filing_processor``'s PDF loading, page slicing, and
excerpt-on-page validation; adds a numeric balance-sheet tie-out as a
soft quality signal. Conservative: rows whose printed text cannot be
found on their cited page are dropped, never guessed.

Run:
    python -m ipo_portal.financials_extractor <issue-slug>
    python -m ipo_portal.financials_extractor --pending [limit]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from .deepseek import DEFAULT_MODEL as DEEPSEEK_DEFAULT_MODEL
from .deepseek import DeepSeekClient
from .filing_processor import (
    DEFAULT_OUTPUT_ROOT,
    LITEPARSE_EXTRACTOR,
    MAX_SLICE_CHARS,
    PDFTOTEXT_EXTRACTOR,
    PDFText,
    _excerpt_on_page,
    _find_excerpt_page,
    _slice_with_page_markers,
    _write_json,
    download_pdf,
    extract_pdf_text,
)
from .orchestrator.metadata import utc_now_iso


SCHEMA_VERSION = "1.0.0"
SLICE_WINDOW_PAGES = 6  # a restated statement table spans ~1-3 pages; pad for safety
MIN_ROWS_PER_STATEMENT = 3

# One spec per statement. Keywords are matched loosely (anywhere on a page at
# or after min_page) because statement titles are not always page headings.
STATEMENT_SPECS: list[dict[str, Any]] = [
    {
        "key": "pnl",
        "title": "Restated Statement of Profit and Loss",
        "keywords": [
            "RESTATED CONSOLIDATED STATEMENT OF PROFIT AND LOSS",
            "RESTATED STATEMENT OF PROFIT AND LOSS",
            "STATEMENT OF PROFIT AND LOSS, AS RESTATED",
            "RESTATED SUMMARY STATEMENT OF PROFIT AND LOSS",
            "RESTATED CONSOLIDATED STATEMENT OF OPERATIONS",
            "PROFIT AND LOSS, AS RESTATED",
        ],
        "min_page": 18,
        "required": True,
    },
    {
        "key": "balance_sheet",
        "title": "Restated Statement of Assets and Liabilities (Balance Sheet)",
        "keywords": [
            "RESTATED CONSOLIDATED STATEMENT OF ASSETS AND LIABILITIES",
            "RESTATED STATEMENT OF ASSETS AND LIABILITIES",
            "STATEMENT OF ASSETS AND LIABILITIES, AS RESTATED",
            "RESTATED SUMMARY STATEMENT OF ASSETS AND LIABILITIES",
            "RESTATED CONSOLIDATED BALANCE SHEET",
            "RESTATED BALANCE SHEET",
        ],
        "min_page": 18,
        "required": True,
    },
    {
        "key": "cash_flow",
        "title": "Restated Statement of Cash Flows",
        "keywords": [
            "RESTATED CONSOLIDATED STATEMENT OF CASH FLOW",
            "RESTATED STATEMENT OF CASH FLOW",
            "STATEMENT OF CASH FLOWS, AS RESTATED",
            "RESTATED SUMMARY STATEMENT OF CASH FLOW",
            "RESTATED CONSOLIDATED CASH FLOW",
            "RESTATED CASH FLOW STATEMENT",
        ],
        "min_page": 18,
        "required": True,
    },
    {
        "key": "ratios",
        "title": "Accounting Ratios (Restated EPS, NAV, RoNW, EBITDA)",
        "keywords": [
            "ACCOUNTING RATIOS",
            "RESTATED ACCOUNTING RATIOS",
            "KEY FINANCIAL RATIOS",
            "SUMMARY OF ACCOUNTING RATIOS",
        ],
        "finder": "ratios",
        "min_page": 18,
        "required": False,
    },
]


STATEMENTS_SYSTEM_PROMPT = """You reconstruct ONE financial statement from a primary Indian filing's restated financial statements into structured JSON.

Hard rules:
1. Output strict JSON only. No prose, no markdown fences.
2. Use only the supplied PDF text slice. No outside knowledge, no inference, no arithmetic of your own.
3. Reproduce the statement line by line, top to bottom, exactly as printed. One JSON row per printed line item.
4. Each row: {"label": "<line item name as printed>", "level": <0 for top-level, 1 for an indented sub-item>, "values": ["<value per period, left to right, exactly as printed including commas and brackets, or null if blank>"], "raw_excerpt": "<the exact contiguous printed line, verbatim>", "source_page": <1-indexed PDF page>}.
5. "values" must align to the "periods" array, same order, same length. Use null for an empty cell. Keep numbers as printed strings (do not convert units, do not strip commas, keep "(123)" for negatives).
6. raw_excerpt must be exact contiguous text from one page (the printed row). Never paraphrase, never join across pages, never use "...". The slice has "--- PDF PAGE N ---" delimiters; use them for source_page but never include them in raw_excerpt.
7. "periods" is the column headers of the statement, left to right, exactly as printed (e.g. "March 31, 2024", "Period ended November 30, 2022"). "unit" is the amount unit as printed (e.g. "₹ in lakhs", "₹ in millions").
8. Include subtotal/total rows (e.g. "Total assets") as their own rows. Skip pure header/section separators that carry no values, unless they are a meaningful sub-heading.
9. If the named statement is not present in the slice, return {"periods": [], "unit": null, "rows": []}.
"""


def _statement_user_prompt(title: str, slice_text: str, start: int, end: int) -> str:
    return (
        f"Reconstruct the **{title}** as structured JSON with this exact shape:\n"
        '{"periods": ["..."], "unit": "<amount unit as printed or null>", '
        '"rows": [{"label": "...", "level": 0, "values": ["...", null], '
        '"raw_excerpt": "<exact printed row>", "source_page": <int>}]}\n\n'
        f"Only reconstruct the {title}. Ignore other statements in the slice.\n"
        f"PDF page range: {start}-{end}. Use exact 1-indexed source_page values.\n\n"
        f"TEXT SLICE:\n{slice_text}"
    )


# --------------------------------------------------------------------- finders


def _find_statement_page(pdf: PDFText, keywords: list[str], min_page: int) -> int | None:
    """Loose finder: first page at/after min_page containing any keyword.

    Skips annexure-index / table-of-contents pages, which list statement
    titles ("Restated Statement of ...") without carrying the actual table.
    """
    needles = [k.casefold() for k in keywords]
    for idx, page in enumerate(pdf.pages, start=1):
        if idx < max(3, min_page):
            continue
        folded = page.casefold()
        if not any(n in folded for n in needles):
            continue
        if _is_index_page(folded):
            continue
        return idx
    return None


def _is_index_page(folded: str) -> bool:
    """A page that merely lists statement titles (annexure index / TOC)."""
    if "table of contents" in folded[:2000]:
        return True
    return folded.count("restated statement of") >= 4


_RATIO_TERMS = ["earnings per share", "net asset value", "return on net worth"]


def _find_ratios_page(pdf: PDFText, min_page: int) -> int | None:
    """Find the actual accounting-ratios table (a cluster of ratio terms),
    preferring the restated annexure over the basis-for-offer-price summary.
    The plain keyword "accounting ratios" matches the annexure index, so we
    look for the table by its contents instead.
    """
    candidates: list[tuple[int, int, int]] = []  # (annexure_pref, hits, page)
    for idx, page in enumerate(pdf.pages, start=1):
        if idx < max(3, min_page):
            continue
        folded = page.casefold()
        hits = sum(1 for t in _RATIO_TERMS if t in folded)
        if hits < 2 or _is_index_page(folded):
            continue
        annexure = 1 if ("restated" in folded and "ratio" in folded) or "annexure" in folded else 0
        candidates.append((annexure, hits, idx))
    if not candidates:
        return None
    candidates.sort(key=lambda c: (-c[0], -c[1], c[2]))
    return candidates[0][2]


def _slice_statement(pdf: PDFText, start: int) -> tuple[str, int, int]:
    end = min(len(pdf.pages), start + SLICE_WINDOW_PAGES)
    text = _slice_with_page_markers(pdf, start, end)
    if len(text) > MAX_SLICE_CHARS:
        text = text[:MAX_SLICE_CHARS]
    return text, start, end


# --------------------------------------------------------------------- numbers


_NUM_RE = re.compile(r"-?\(?\d[\d,]*\.?\d*\)?")


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"-", "—", "NA", "N.A.", "nil", "Nil"}:
        return None
    m = _NUM_RE.search(text)
    if not m:
        return None
    raw = m.group(0)
    negative = raw.startswith("(") and raw.endswith(")")
    raw = raw.strip("()").replace(",", "")
    try:
        num = float(raw)
    except ValueError:
        return None
    return -num if negative else num


# --------------------------------------------------------------------- extract


def _extract_statement(
    spec: dict[str, Any],
    pdf: PDFText,
    client: DeepSeekClient,
    model: str,
    slug: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (statement, call_meta) for one spec; statement may be empty."""
    if spec.get("finder") == "ratios":
        start = _find_ratios_page(pdf, spec["min_page"])
    else:
        start = _find_statement_page(pdf, spec["keywords"], spec["min_page"])
    if start is None:
        return (
            {"title": spec["title"], "periods": [], "unit": None, "rows": [], "found": False},
            {"statement": spec["key"], "slice_method": "not_found"},
        )
    slice_text, start, end = _slice_statement(pdf, start)
    response = client.chat(
        user=_statement_user_prompt(spec["title"], slice_text, start, end),
        system=STATEMENTS_SYSTEM_PROMPT,
        model=model,
        temperature=0.0,
        response_format="json_object",
        max_tokens=24_000,
        purpose=f"v3_financials:{slug}:{spec['key']}",
    )
    body = response.json_content if isinstance(response.json_content, dict) else {}
    periods = [str(p) for p in (body.get("periods") or [])]
    unit = body.get("unit")
    rows_in = body.get("rows") or []

    rows_out: list[dict[str, Any]] = []
    checked = repaired = dropped = 0
    for row in rows_in:
        if not isinstance(row, dict):
            continue
        excerpt = row.get("raw_excerpt")
        page = row.get("source_page")
        if not isinstance(excerpt, str) or not excerpt.strip():
            dropped += 1
            continue
        checked += 1
        if isinstance(page, int) and _excerpt_on_page(pdf, excerpt, page):
            pass
        else:
            found = _find_excerpt_page(pdf, excerpt)
            if found is None:
                dropped += 1
                continue
            page = found
            repaired += 1
        values = row.get("values")
        rows_out.append(
            {
                "label": str(row.get("label") or "").strip(),
                "level": int(row.get("level") or 0),
                "values": list(values) if isinstance(values, list) else [],
                "raw_excerpt": excerpt.strip(),
                "source_page": page,
            }
        )

    statement = {
        "title": spec["title"],
        "periods": periods,
        "unit": unit,
        "rows": rows_out,
        "found": True,
        "validation": {"checked": checked, "repaired": repaired, "dropped": dropped},
    }
    if spec.get("key") == "ratios":
        _pivot_ratio_periods(statement)
    call_meta = {
        "statement": spec["key"],
        "slice_method": "keyword",
        "page_range": [start, end],
        "model": response.model,
        "cached": response.cached,
        "prompt_tokens": response.prompt_tokens,
        "completion_tokens": response.completion_tokens,
        "estimated_cost_usd": round(response.estimated_cost_usd, 6),
        "rows_in": len(rows_in),
        "rows_kept": len(rows_out),
    }
    return statement, call_meta


def _period_key(s: str) -> str:
    s = (s or "").lower()
    for phrase in ("as on", "as at", "for the year ended", "for the period ended", "year ended", "period ended"):
        s = s.replace(phrase, "")
    return re.sub(r"[^a-z0-9]", "", s)


def _pivot_ratio_periods(statement: dict[str, Any]) -> None:
    """Collapse a diagonal ratios layout into one row per ratio.

    Restated ratio annexures often list each ratio name then a sub-row per
    period ("As on November 30, 2022 ... 17.55%"). The model reproduces that
    verbatim, leaving values on the diagonal. Here we fold each period sub-row
    into the preceding ratio's values at the matching column.
    """
    periods = statement.get("periods") or []
    rows = statement.get("rows") or []
    if not periods or not rows:
        return
    pkey = {_period_key(p): i for i, p in enumerate(periods) if _period_key(p)}
    out: list[dict[str, Any]] = []
    for r in rows:
        col = pkey.get(_period_key(r.get("label") or ""))
        if col is not None and out:
            vals = r.get("values") or []
            val = vals[col] if col < len(vals) and vals[col] not in (None, "") else next(
                (v for v in vals if v not in (None, "")), None
            )
            if val is not None:
                host = out[-1]
                hv = host.get("values") or []
                hv = list(hv) + [None] * (len(periods) - len(hv))
                if hv[col] in (None, ""):
                    hv[col] = val
                host["values"] = hv
                continue  # drop the folded period sub-row
        out.append(r)
    # Drop orphan rows that carry no data at all (ratio headers whose only
    # content was a label, after folding). "-" cells are real disclosures, kept.
    statement["rows"] = [r for r in out if any(v not in (None, "") for v in (r.get("values") or []))]


def _balance_sheet_tie_out(statement: dict[str, Any]) -> dict[str, Any] | None:
    """Soft check: total assets must equal total equity + liabilities.

    Restated balance sheets label these two grand totals inconsistently — a
    bare "TOTAL" for the assets side, "Total Equity and Liabilities" (often
    with OCR typos) for the other. We identify both robustly and compare
    across every reported period, not just one.
    """
    rows = statement.get("rows") or []
    periods = statement.get("periods") or []
    if not rows or not periods:
        return None

    def _is_total(label: str) -> bool:
        l = re.sub(r"^\s*\([a-z0-9]+\)\s*", "", label.strip().casefold())  # drop a "(a)"/"(i)" marker only
        return l.startswith("total")

    def _is_liab_side(label: str) -> bool:
        l = label.casefold()
        return "equit" in l or "liabilit" in l or "abiliti" in l  # tolerate "IABILITIES" typo

    # Grand totals are top-level rows; ignore sub-line "total outstanding dues ..." (level 1).
    total_rows = [r for r in rows if int(r.get("level") or 0) == 0 and _is_total(r.get("label") or "")]
    liab = next((r for r in total_rows if _is_liab_side(r.get("label") or "")), None)
    assets = next((r for r in total_rows if "asset" in (r.get("label") or "").casefold()), None)
    if assets is None:
        # Bare "TOTAL" on the assets side: the total row that isn't the liab total.
        others = [r for r in total_rows if r is not liab]
        assets = others[-1] if others else None
    if liab is None or assets is None or liab is assets:
        return {"state": "indeterminate"}

    def _val(row: dict[str, Any], col: int) -> float | None:
        vals = row.get("values") or []
        return _to_float(vals[col]) if col < len(vals) else None

    mismatches: list[dict[str, Any]] = []
    checked = 0
    for col in range(len(periods)):
        a, l = _val(assets, col), _val(liab, col)
        if a is None or l is None:
            continue
        checked += 1
        if abs(a - l) > max(1.0, abs(a) * 0.005):  # 0.5% rounding tolerance
            mismatches.append({"period": periods[col], "assets": a, "equity_and_liabilities": l})
    if not checked:
        return {"state": "indeterminate"}
    if mismatches:
        return {"state": "mismatch", "periods_checked": checked, "mismatches": mismatches[:4]}
    return {"state": "ok", "periods_checked": checked}


def extract_financials(
    slug: str,
    *,
    issues_root: Path = DEFAULT_OUTPUT_ROOT,
    client: DeepSeekClient | None = None,
    model: str = DEEPSEEK_DEFAULT_MODEL,
) -> dict[str, Any]:
    """Extract all statements for one issue and write financials.json."""
    facts_path = issues_root / slug / "prospectus_facts.json"
    if not facts_path.exists():
        raise ValueError(f"No prospectus_facts.json for {slug!r}; extract facts first.")
    facts = json.loads(facts_path.read_text(encoding="utf-8"))
    document_url = facts.get("document_url")
    document_type = facts.get("document_type", "DRHP")
    backend = facts.get("pdf_text_extractor") or PDFTOTEXT_EXTRACTOR
    if backend not in {PDFTOTEXT_EXTRACTOR, LITEPARSE_EXTRACTOR}:
        backend = PDFTOTEXT_EXTRACTOR

    local_pdf = facts.get("local_pdf_path")
    pdf_path = Path(local_pdf) if local_pdf else None
    if not pdf_path or not pdf_path.exists():
        if not document_url:
            raise ValueError(f"No local or remote PDF available for {slug!r}.")
        _, pdf_path = download_pdf(document_url)
    pdf = extract_pdf_text(pdf_path, backend=backend, use_cache=True)

    client = client or DeepSeekClient()
    statements: dict[str, Any] = {}
    calls: list[dict[str, Any]] = []
    total_cost = 0.0
    for spec in STATEMENT_SPECS:
        statement, meta = _extract_statement(spec, pdf, client, model, slug)
        statements[spec["key"]] = statement
        calls.append(meta)
        total_cost += float(meta.get("estimated_cost_usd") or 0.0)

    # quality
    missing_required = [
        s["key"] for s in STATEMENT_SPECS
        if s["required"] and len(statements[s["key"]].get("rows") or []) < MIN_ROWS_PER_STATEMENT
    ]
    tie_out = _balance_sheet_tie_out(statements.get("balance_sheet") or {})
    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if missing_required:
        failures.append({"code": "missing_required_statements", "statements": missing_required})
    if tie_out and tie_out.get("state") == "mismatch":
        warnings.append({"code": "balance_sheet_tie_out_mismatch", **tie_out})
    elif tie_out and tie_out.get("state") == "indeterminate":
        warnings.append({"code": "balance_sheet_tie_out_indeterminate"})
    state = "fail" if failures else "review" if warnings else "pass"

    canonical_periods = (statements.get("pnl") or {}).get("periods") or []
    canonical_unit = (statements.get("pnl") or {}).get("unit")

    doc = {
        "$schema": "https://ipo-watch.local/schema/v3/financials.schema.json",
        "schema_version": SCHEMA_VERSION,
        "slug": slug,
        "document_type": document_type,
        "document_url": document_url,
        "extracted_at": utc_now_iso(),
        "currency_unit": canonical_unit,
        "periods": canonical_periods,
        "statements": statements,
        "quality": {"state": state, "failures": failures, "warnings": warnings},
        "balance_sheet_tie_out": tie_out,
        "deepseek": {
            "used": any(c.get("model") for c in calls),
            "model": next((c.get("model") for c in calls if c.get("model")), None),
            "calls": calls,
            "cost_usd": f"{total_cost:.6f}",
        },
    }
    _write_json(issues_root / slug / "financials.json", doc)
    return {
        "slug": slug,
        "state": state,
        "rows": {k: len(v.get("rows") or []) for k, v in statements.items()},
        "cost_usd": round(total_cost, 6),
    }


# --------------------------------------------------------------------- driver


def pending_financials_slugs(*, issues_root: Path = DEFAULT_OUTPUT_ROOT) -> list[str]:
    """Issues with a usable extraction whose financials.json is missing/stale."""
    out: list[str] = []
    for facts_file in sorted(issues_root.glob("*/prospectus_facts.json")):
        doc = json.loads(facts_file.read_text(encoding="utf-8"))
        if not doc.get("facts") or not (doc.get("quality", {}).get("verified_fact_count") or 0):
            continue
        slug = facts_file.parent.name
        core_file = facts_file.parent / "core.json"
        if core_file.exists():
            try:
                core = json.loads(core_file.read_text(encoding="utf-8"))
                if (core.get("identity") or {}).get("status") == "Listed":
                    continue  # go-forward only: skip already-listed old filings
            except (OSError, json.JSONDecodeError):
                pass
        fin = facts_file.parent / "financials.json"
        if not fin.exists() or facts_file.stat().st_mtime > fin.stat().st_mtime:
            out.append(slug)
    return out


def generate_pending_financials(
    *, limit: int | None = None, issues_root: Path = DEFAULT_OUTPUT_ROOT
) -> dict[str, Any]:
    slugs = pending_financials_slugs(issues_root=issues_root)
    if limit is not None:
        slugs = slugs[:limit]
    client = DeepSeekClient() if slugs else None
    summary: dict[str, Any] = {"pending": len(slugs), "done": [], "failed": []}
    for slug in slugs:
        try:
            res = extract_financials(slug, issues_root=issues_root, client=client)
            summary["done"].append(res)
        except Exception as exc:  # one bad filing must not stop the rest
            summary["failed"].append({"slug": slug, "error": f"{type(exc).__name__}: {exc}"})
    return summary


def _main(argv: list[str]) -> int:
    if argv and argv[0] == "--pending":
        limit = int(argv[1]) if len(argv) > 1 else None
        print(json.dumps(generate_pending_financials(limit=limit), indent=2))
        return 0
    if not argv:
        print(
            "usage:\n"
            "  python -m ipo_portal.financials_extractor <issue-slug>\n"
            "  python -m ipo_portal.financials_extractor --pending [limit]",
            file=sys.stderr,
        )
        return 2
    res = extract_financials(argv[0])
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
