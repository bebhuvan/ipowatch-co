"""Parse NSE ``corporates/offerdocs`` (equity + sme).

Catalogs:
* ``docs/schema/raw_catalog/nse/offer_documents_equity.json``
* ``docs/schema/raw_catalog/nse/offer_documents_sme.json``

Each row carries URLs to canonical issue documents:

* ``drhpAttach``  → DRHP PDF
* ``rhpAttach``   → RHP PDF
* ``fpAttach``    → Final Prospectus PDF
* ``advAttach``   → Mandatory public advertisement PDF
* ``isin``        → ISIN of the issue (often null for older SME rows)
* ``pan_no``      → Issuer PAN
* ``symbol``      → NSE trading symbol (often ``"-"`` until listed)
* ``issue_open_date`` / ``issue_close_date`` — often ``"-"`` until set

This is the canonical source for ``documents.{drhp,rhp,prospectus}_url``
fields on the v2 issue record. Identifiers are stronger here than in
the public-past-issues endpoint (we get ISIN + PAN), so when present
they win the join.

The same row shape is used for both mainboard and SME — one parser,
two registrations.
"""

from __future__ import annotations

from typing import Any

from ...normalization import (
    UnitParseError,
    clean_text,
    parse_indian_date,
    sanitize_plaintext,
)
from ..identity import build_identity
from ..pipeline import Contribution, IssueJoinKey
from .registry import ParserContext, register_parser


@register_parser("nse", "offer_documents_equity")
def parse_equity(body: Any, ctx: ParserContext) -> list[Contribution]:
    return _parse(body, ctx, board_type="Main Board")


@register_parser("nse", "offer_documents_sme")
def parse_sme(body: Any, ctx: ParserContext) -> list[Contribution]:
    return _parse(body, ctx, board_type="SME Board")


def _parse(body: Any, ctx: ParserContext, *, board_type: str) -> list[Contribution]:
    if not isinstance(body, list):
        return []
    out: list[Contribution] = []
    for row in body:
        if not isinstance(row, dict):
            continue
        contribution = _row_to_contribution(row, ctx, board_type=board_type)
        if contribution is not None:
            out.append(contribution)
    return out


def _row_to_contribution(
    row: dict[str, Any],
    ctx: ParserContext,
    *,
    board_type: str,
) -> Contribution | None:
    company_name = sanitize_plaintext(row.get("company"))
    if not company_name:
        return None

    # Best anchor date for the listing year: issue_open_date > drhpDate > fpDate.
    anchor_date = (
        _safe_date(row.get("issue_open_date"))
        or _safe_date(row.get("issue_close_date"))
        or _safe_date(row.get("drhpDate"))
        or _safe_date(row.get("fpDate"))
        or _safe_date(row.get("advDate"))
    )
    if anchor_date is None:
        # No date at all — we still record the docs but can't join cleanly.
        # Skip; the document URLs will be picked up next snapshot when an
        # issue_open_date is published.
        return None

    isin = _safe_id(row.get("isin"))
    pan = _safe_id(row.get("pan_no"))
    identity = build_identity(
        company_name=company_name,
        isin=isin,
        pan=pan,
        listing_year=anchor_date.year,
    )
    discriminator, _, value = identity.stable_join_key.partition(":")
    join_key = IssueJoinKey(discriminator=discriminator, value=value)

    symbol = _safe_id(row.get("symbol"))

    documents: dict[str, str] = {}
    drhp = _safe_url(row.get("drhpAttach"))
    rhp = _safe_url(row.get("rhpAttach"))
    fp = _safe_url(row.get("fpAttach"))
    adv = _safe_url(row.get("advAttach"))
    if drhp:
        documents["drhp_url"] = drhp
    if rhp:
        documents["rhp_url"] = rhp
    if fp:
        documents["prospectus_url"] = fp
    if adv:
        documents["basis_allotment_url"] = adv

    fields: dict[str, Any] = {
        "identity.company_name": company_name,
        "identity.slug": identity.slug,
        "identity.isin": isin,
        "identity.symbol": symbol,
        "identity.board_type": board_type,
        "identity.issue_type": "IPO",
    }
    # Only attach timeline if the dates were real (not "-"). Status is left
    # for the pipeline's _infer_status pass: a document-index row with no
    # dates but a prospectus URL resolves to "Filed" there, after any
    # primary-tier source has had its say.
    open_date = _safe_date(row.get("issue_open_date"))
    close_date = _safe_date(row.get("issue_close_date"))
    if open_date:
        fields["timeline.open_date"] = open_date.isoformat()
    if close_date:
        fields["timeline.close_date"] = close_date.isoformat()

    if documents:
        # Merge as a single nested object — precedence will pick this whole
        # block vs other sources. The dotted-path merge isn't deep-merging
        # documents.*, so we attach each URL under its top-level path.
        for k, v in documents.items():
            fields[f"documents.{k}"] = v

    fields = {k: v for k, v in fields.items() if v is not None}

    return Contribution(
        source=ctx.source,
        endpoint=ctx.endpoint,
        snapshot_at=ctx.snapshot_at,
        join_key=join_key,
        fields=fields,
    )


def _safe_date(value: Any):
    text = clean_text(value)
    if text is None:
        return None
    try:
        return parse_indian_date(text)
    except UnitParseError:
        return None


def _safe_id(value: Any) -> str | None:
    """Return a clean identifier or ``None`` for ``"-"`` / blank sentinels."""
    text = clean_text(value)
    if text is None:
        return None
    if text in {"-", "--", "NA"}:
        return None
    return text


def _safe_url(value: Any) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    if not (text.startswith("http://") or text.startswith("https://")):
        return None
    return text
