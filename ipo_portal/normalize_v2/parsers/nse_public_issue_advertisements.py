"""Parse NSE public issue advertisement filings.

This endpoint is a live/near-live filing surface for IPO/FPO public issue
advertisements. Most nested records are advertisements, not prospectuses, so
we do not map them into ``documents.drhp_url``/``rhp_url``. The useful
canonical signal is that a company has a fresh filed issue, with board/type
and draft/submit dates for joining.
"""

from __future__ import annotations

from typing import Any

from ...normalization import UnitParseError, clean_text, parse_indian_date, sanitize_plaintext
from ..identity import build_identity
from ..pipeline import Contribution, IssueJoinKey
from .registry import ParserContext, register_parser


@register_parser("nse", "public_issue_advertisements")
def parse(body: Any, ctx: ParserContext) -> list[Contribution]:
    if not isinstance(body, list):
        return []
    out: list[Contribution] = []
    for row in body:
        if not isinstance(row, dict):
            continue
        contribution = _row_to_contribution(row, ctx)
        if contribution is not None:
            out.append(contribution)
    return out


def _row_to_contribution(row: dict[str, Any], ctx: ParserContext) -> Contribution | None:
    company = sanitize_plaintext(row.get("issuerName"))
    if not company:
        return None
    anchor_date = _safe_date(row.get("draftDate")) or _nested_date(row) or _ctx_date(ctx.snapshot_at)
    if anchor_date is None:
        return None

    identity = build_identity(company_name=company, listing_year=anchor_date.year)
    discriminator, _, value = identity.stable_join_key.partition(":")
    join_key = IssueJoinKey(discriminator=discriminator, value=value)

    fields: dict[str, Any] = {
        "identity.company_name": company,
        "identity.slug": identity.slug,
        "identity.issue_type": _issue_type(row.get("issueType")),
        "identity.board_type": _board_type(row.get("boardType")),
        "identity.status": "Filed",
    }
    fields = {k: v for k, v in fields.items() if v is not None}
    return Contribution(
        source=ctx.source,
        endpoint=ctx.endpoint,
        snapshot_at=ctx.snapshot_at,
        join_key=join_key,
        fields=fields,
    )


def _nested_date(row: dict[str, Any]):
    for key in ("record10", "record20", "record30", "record40", "record50", "record60", "record70", "record80", "record90"):
        records = row.get(key)
        if not isinstance(records, list):
            continue
        for item in records:
            if not isinstance(item, dict):
                continue
            parsed = _safe_date(item.get("draftDate")) or _safe_date(item.get("submitDate"))
            if parsed is not None:
                return parsed
    return None


def _safe_date(value: Any):
    text = clean_text(value)
    if text is None:
        return None
    try:
        return parse_indian_date(text)
    except UnitParseError:
        return None


def _ctx_date(value: str):
    try:
        return parse_indian_date(value[:10])
    except UnitParseError:
        return None


def _issue_type(value: Any) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    upper = text.upper()
    if upper in {"IPO", "FPO", "OFS"}:
        return upper
    if "RIGHT" in upper:
        return "Rights"
    if "BUY" in upper:
        return "Buyback"
    return "Others"


def _board_type(value: Any) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    upper = text.upper()
    if "SME" in upper:
        return "SME Board"
    if "MAIN" in upper:
        return "Main Board"
    return None
