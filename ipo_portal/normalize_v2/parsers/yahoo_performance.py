"""Parse Yahoo Finance performance snapshots into v2 contributions."""

from __future__ import annotations

from typing import Any

from ...normalization import UnitParseError, parse_indian_date, sanitize_plaintext
from ..identity import build_identity
from ..pipeline import Contribution, IssueJoinKey
from .registry import ParserContext, register_parser


@register_parser("yahoo", "performance")
def parse(body: Any, ctx: ParserContext) -> list[Contribution]:
    if not isinstance(body, list):
        return []
    out: list[Contribution] = []
    for row in body:
        if not isinstance(row, dict) or row.get("status") not in {"ok", "no_prices"}:
            continue
        contribution = _row_to_contribution(row, ctx)
        if contribution is not None:
            out.append(contribution)
    return out


def _row_to_contribution(row: dict[str, Any], ctx: ParserContext) -> Contribution | None:
    company = sanitize_plaintext(row.get("company_name"))
    listing_date = _safe_date(row.get("listing_date"))
    if not company or listing_date is None:
        return None

    identity = build_identity(company_name=company, listing_year=listing_date.year)
    discriminator, _, value = identity.stable_join_key.partition(":")
    join_key = IssueJoinKey(discriminator=discriminator, value=value)

    symbol = sanitize_plaintext(row.get("symbol"))
    yahoo_symbol = sanitize_plaintext(row.get("yahoo_symbol"))
    aliases = [f"yahoo:{yahoo_symbol}"] if yahoo_symbol else None

    fields: dict[str, Any] = {
        "identity.company_name": company,
        "identity.slug": identity.slug,
        "identity.issue_type": "IPO",
        "identity.symbol": symbol,
        "identity.board_type": row.get("board_type"),
        "identity.aliases": aliases,
        "timeline.listing_date": listing_date.isoformat(),
        "pricing.issue_price_paise": row.get("issue_price_paise"),
        "listing_performance.listing_open_price_paise": row.get("listing_open_paise"),
        "listing_performance.listing_close_price_paise": row.get("listing_close_paise"),
        "listing_performance.current_price_paise": row.get("current_price_paise"),
        "listing_performance.listing_gain_bps": row.get("listing_gain_bps"),
        "listing_performance.current_gain_bps": row.get("current_gain_from_issue_bps"),
    }
    fields = {k: v for k, v in fields.items() if v is not None}
    return Contribution(
        source=ctx.source,
        endpoint=ctx.endpoint,
        snapshot_at=ctx.snapshot_at,
        join_key=join_key,
        fields=fields,
    )


def _safe_date(value: Any):
    try:
        return parse_indian_date(value)
    except UnitParseError:
        return None
