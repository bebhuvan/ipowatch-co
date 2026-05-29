"""Adapter: turn an issue's extracted facts into a report brief + corpus.

This is the bridge between the *extraction* layer (DeepSeek 7-section
``prospectus_facts.json`` with page-cited, citation-validated facts) and
the *generation* layer (the academy-style outline -> draft -> factcheck ->
edit -> visual pipeline, reused for IPO research reports).

It produces three things for one issue slug:

* ``brief``        — an :class:`IPOReportBrief`: the company, the offer
                     basics, the questions the report must answer, and the
                     lines it must not cross (no investment advice, no
                     invented numbers). Anchors the pipeline's intent.
* ``facts_digest`` — the surviving facts organised by the seven sections,
                     each carrying its value, verbatim DRHP excerpt, page,
                     and confidence. This is what the outline and draft
                     stages read to write grounded prose.
* ``corpus``       — a list of :class:`SourceDoc` (reused from the academy
                     pipeline), one per cited DRHP page, excerpts verbatim.
                     This is what the fact-check stage verifies ``[^drhp-pNN]``
                     citations against.

Only facts that survived citation validation (``value is not None``) are
eligible. Redacted leaves are dropped, never guessed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .academy import SourceDoc
from .metadata import utc_now_iso


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_ISSUES_ROOT = PROJECT_ROOT / "data" / "ipo_watch_v3" / "issues"

# The seven extraction sections, in the order a research report reads best.
SECTION_ORDER = (
    "business",
    "industry",
    "offer",
    "financials",
    "valuation_and_peers",
    "governance",
    "risks",
)

SECTION_LABELS = {
    "business": "Business",
    "industry": "Industry and market",
    "offer": "The offer",
    "financials": "Financials",
    "valuation_and_peers": "Valuation and peers",
    "governance": "Governance and shareholding",
    "risks": "Risks",
}


@dataclass(frozen=True)
class IPOReportBrief:
    """The brief that anchors one IPO research report.

    Mirrors the shape of the academy ``ArticleBrief`` but scoped to a
    company filing rather than an evergreen explainer.
    """

    slug: str
    issue_slug: str
    company_name: str
    document_type: str
    working_title: str
    dek: str
    audience_one_line: str
    offer_summary: str
    listing_context: str
    must_answer: list[str]
    must_not_say: list[str]
    length_target_words: int
    source_ids: list[str]
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "issue_slug": self.issue_slug,
            "company_name": self.company_name,
            "document_type": self.document_type,
            "working_title": self.working_title,
            "dek": self.dek,
            "audience_one_line": self.audience_one_line,
            "offer_summary": self.offer_summary,
            "listing_context": self.listing_context,
            "must_answer": list(self.must_answer),
            "must_not_say": list(self.must_not_say),
            "length_target_words": self.length_target_words,
            "source_ids": list(self.source_ids),
            "notes": self.notes,
        }


@dataclass
class ReportInputs:
    """Everything the pipeline needs for one report, produced by the adapter."""

    brief: IPOReportBrief
    facts_digest: dict[str, list[dict[str, Any]]]
    corpus: list[SourceDoc]
    documents: list[dict[str, Any]]

    def facts_digest_json(self) -> str:
        return json.dumps(self.facts_digest, ensure_ascii=False, separators=(",", ":"))


# ----------------------------------------------------------------- helpers


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _is_leaf(node: Any) -> bool:
    return isinstance(node, dict) and "value" in node and "raw_excerpt" in node


def _humanise(field_name: str) -> str:
    return field_name.replace("_", " ").strip().capitalize()


def _rupees_from_paise(paise: Any) -> str | None:
    try:
        rupees = int(paise) / 100.0
    except (TypeError, ValueError):
        return None
    if rupees.is_integer():
        return f"₹{int(rupees):,}"
    return f"₹{rupees:,.2f}"


def _iter_leaves(node: Any):
    """Yield every fact-leaf dict under ``node`` (leaf, list, or nested dict)."""
    if _is_leaf(node):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _iter_leaves(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_leaves(value)


def _section_fields(facts: dict[str, Any], section: str) -> dict[str, Any]:
    """Unwrap the double nesting ``facts[section][section]`` defensively."""
    outer = facts.get(section)
    if not isinstance(outer, dict):
        return {}
    inner = outer.get(section)
    if isinstance(inner, dict):
        return inner
    return outer


# ----------------------------------------------------------------- build


def build_report_inputs(
    slug: str,
    *,
    issues_root: Path = DEFAULT_ISSUES_ROOT,
) -> ReportInputs:
    """Assemble the brief, facts digest, and citation corpus for ``slug``."""
    issue_dir = issues_root / slug
    facts_doc = _load(issue_dir / "prospectus_facts.json")
    if not facts_doc or not facts_doc.get("facts"):
        raise ValueError(
            f"No usable prospectus_facts.json for {slug!r} at {issue_dir}. "
            f"Run the extraction (filing_processor) first."
        )

    core = _load(issue_dir / "core.json")
    filings = _load(issue_dir / "filings.json")
    market = _load(issue_dir / "market.json")

    identity = core.get("identity", {})
    company_name = (
        identity.get("company_name")
        or facts_doc.get("slug", slug).replace("-", " ").title()
    )
    document_type = facts_doc.get("document_type", "DRHP")
    document_url = facts_doc.get("document_url")

    # Prefer the DRHP/prospectus document URL from filings.json as the
    # canonical citation target; fall back to the one on the facts doc.
    doc_url_by_type: dict[str, str] = {}
    for d in filings.get("documents", []) or []:
        if d.get("type") and d.get("url"):
            doc_url_by_type.setdefault(d["type"], d["url"])
    citation_url = (
        doc_url_by_type.get(document_type)
        or doc_url_by_type.get("DRHP")
        or doc_url_by_type.get("Prospectus")
        or document_url
    )

    facts = facts_doc["facts"]

    # --- facts digest (by section) + per-page excerpt map ---------------
    facts_digest: dict[str, list[dict[str, Any]]] = {}
    page_excerpts: dict[int, list[str]] = {}
    seen_excerpt_per_page: dict[int, set[str]] = {}

    for section in SECTION_ORDER:
        fields = _section_fields(facts, section)
        entries: list[dict[str, Any]] = []
        for field_name, node in fields.items():
            for leaf in _iter_leaves(node):
                value = leaf.get("value")
                if value is None:
                    continue  # redacted / unverified — drop, never guess
                page = leaf.get("source_page")
                excerpt = (leaf.get("raw_excerpt") or "").strip()
                entry = {
                    "label": _humanise(field_name),
                    "value": value,
                    "excerpt": excerpt,
                    "page": page,
                    "section_heading": leaf.get("source_section"),
                    "confidence": leaf.get("confidence"),
                }
                entries.append(entry)
                if isinstance(page, int) and excerpt:
                    bucket = seen_excerpt_per_page.setdefault(page, set())
                    if excerpt not in bucket:
                        bucket.add(excerpt)
                        page_excerpts.setdefault(page, []).append(excerpt)
        if entries:
            facts_digest[section] = entries

    # --- corpus: one SourceDoc per cited DRHP page ----------------------
    now = utc_now_iso()
    extracted_at = facts_doc.get("extracted_at", now)
    corpus: list[SourceDoc] = []
    for page in sorted(page_excerpts):
        corpus.append(
            SourceDoc(
                source_id=f"drhp-p{page}",
                title=f"{document_type}, page {page}",
                url=citation_url or "https://www.sebi.gov.in/",
                publisher=f"{company_name} — {document_type} filing",
                published_at=None,
                fetched_at=extracted_at,
                source_tier="primary",
                document_kind="filing",
                excerpts=page_excerpts[page],
            )
        )
    source_ids = [doc.source_id for doc in corpus]

    # --- offer + listing context strings for the brief ------------------
    offer_fields = _section_fields(facts, "offer")
    offer_bits: list[str] = []
    for key in ("issue_structure", "fresh_issue", "offer_for_sale"):
        node = offer_fields.get(key)
        if _is_leaf(node) and node.get("value") is not None:
            offer_bits.append(f"{_humanise(key)}: {node['value']}")
    issue_price = _rupees_from_paise(core.get("pricing", {}).get("issue_price_paise"))
    if issue_price:
        offer_bits.append(f"Issue price: {issue_price} per share")
    offer_summary = "; ".join(offer_bits) or "See the offer facts in the digest."

    listing_bits: list[str] = []
    board = identity.get("board_type")
    if board:
        listing_bits.append(board)
    listing_date = (core.get("timeline", {}) or {}).get("listing_date")
    if listing_date:
        listing_bits.append(f"listed {listing_date}")
    if market.get("current_price_paise") is not None:
        cur = _rupees_from_paise(market["current_price_paise"])
        if cur:
            listing_bits.append(f"recent price {cur}")
    listing_context = "; ".join(listing_bits) or "Pre-listing filing."

    brief = IPOReportBrief(
        slug=f"{slug}-report",
        issue_slug=slug,
        company_name=company_name,
        document_type=document_type,
        working_title=f"{company_name}: inside the {document_type}",
        dek=(
            f"What the {document_type} discloses about {company_name}'s business, "
            f"offer, financials and risks — sourced to the filing, page by page."
        ),
        audience_one_line=(
            "a retail investor deciding whether this IPO is worth their time, "
            "who wants the filing explained plainly and accurately"
        ),
        offer_summary=offer_summary,
        listing_context=listing_context,
        must_answer=[
            "What does the company actually do, and how does it make money?",
            "What is the offer — fresh issue versus offer for sale, and what are the stated objects (use of proceeds)?",
            "What do the disclosed financials show across the reported periods (revenue, profitability, debt)?",
            "What industry is it in, and what are the demand drivers and headwinds the filing names?",
            "How is it valued, and against which listed peers?",
            "Who controls the company (promoters, shareholding), and what does the governance and litigation disclosure show?",
            "What are the most material risks the filing itself flags?",
        ],
        must_not_say=[
            "No investment recommendation, verdict, or 'subscribe / avoid' call.",
            "No price target, return forecast, or listing-gain prediction.",
            "No number, date, or name that is not in the filing excerpts — every specific must carry a [^drhp-pNN] citation.",
            "Redacted or undisclosed values ([●], blanks) must be described as not disclosed, never estimated or filled in.",
            "No claim about subscription, allotment, or post-listing outcome unless the filing states it.",
        ],
        length_target_words=1900,
        source_ids=source_ids,
        notes=(
            f"Extraction quality: {facts_doc.get('quality', {}).get('state')}, "
            f"{facts_doc.get('quality', {}).get('verified_fact_count')} verified facts."
        ),
    )

    documents = [
        {
            "source_id": (d.get("type") or "filing").lower().replace(" ", "-"),
            "title": d.get("type") or "Filing",
            "url": d.get("url"),
            "publisher": f"{company_name} / NSE-BSE",
            "document_kind": "filing",
            "source_tier": "primary",
        }
        for d in filings.get("documents", []) or []
        if d.get("url")
    ]

    return ReportInputs(
        brief=brief,
        facts_digest=facts_digest,
        corpus=corpus,
        documents=documents,
    )
