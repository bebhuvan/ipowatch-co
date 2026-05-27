"""Parse the NSE ``all-upcoming-issues?category=ipo`` JSON list.

Catalog reference: ``docs/schema/raw_catalog/nse/ipo_upcoming.json``.

The response is a list of forthcoming IPOs that have not yet opened.
Field shape is identical to ``ipo_current_issue`` except the status is
"Forthcoming" / "Upcoming" rather than "Active", and subscription
numbers are absent. We reuse the same row→contribution mapping but
clamp status to ``Upcoming``.
"""

from __future__ import annotations

from typing import Any

from ...normalization import (
    UnitParseError,
    coerce_bool,
    coerce_int,
    parse_indian_date,
    parse_subscription_multiple,
)
from ..identity import build_identity
from ..pipeline import Contribution, IssueJoinKey
from .registry import ParserContext, register_parser


@register_parser("nse", "ipo_upcoming")
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
    company_name = (row.get("companyName") or row.get("company") or "").strip()
    if not company_name:
        return None
    open_date = _safe_date(row.get("issueStartDate") or row.get("ipoStartDate"))
    close_date = _safe_date(row.get("issueEndDate") or row.get("ipoEndDate"))

    listing_year = (open_date or close_date).year if (open_date or close_date) else None
    if listing_year is None:
        return None

    identity = build_identity(
        company_name=company_name,
        listing_year=listing_year,
    )
    discriminator, _, value = identity.stable_join_key.partition(":")
    join_key = IssueJoinKey(discriminator=discriminator, value=value)

    aliases: list[str] = []
    is_bse = _try_coerce_bool(row.get("isBse"))
    if is_bse and row.get("symbol"):
        aliases.append(f"bse:cross_listed:{row['symbol']}")

    fields: dict[str, Any] = {
        "identity.company_name": company_name,
        "identity.slug": identity.slug,
        "identity.symbol": (row.get("symbol") or None) or None,
        "identity.board_type": _board_type(row.get("series") or row.get("securityType")),
        # Status is forced to Upcoming regardless of any string the API emits
        # — this endpoint, by definition, returns only forthcoming issues.
        "identity.status": "Upcoming",
        "identity.issue_type": "IPO",
        "timeline.open_date": open_date.isoformat() if open_date else None,
        "timeline.close_date": close_date.isoformat() if close_date else None,
        "pricing.issue_size_shares": _safe_int(row.get("noOfSharesOffered")),
        "subscription.overall_times_x": _safe_times(row.get("noOfTime")),
        "identity.aliases": aliases or None,
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


def _safe_int(value: Any):
    try:
        return coerce_int(value)
    except UnitParseError:
        return None


def _safe_times(value: Any):
    try:
        dec = parse_subscription_multiple(value)
    except UnitParseError:
        return None
    return None if dec is None else str(dec)


def _try_coerce_bool(value: Any) -> bool | None:
    try:
        return coerce_bool(value)
    except UnitParseError:
        return None


def _board_type(series: Any) -> str | None:
    text = str(series or "").strip().upper()
    if text == "SME":
        return "SME Board"
    if text in ("EQ", "BE", "B1", "B2", "IPO", ""):
        return "Main Board"
    return None
