"""Parse the NSE ``ipo-current-issue`` JSON list.

Schema reference: ``docs/schema/v2/issue.schema.json``.
Catalog reference: ``docs/schema/raw_catalog/nse/ipo_current_issue.json``.

Sample row (live NSE response):

.. code-block:: json

    {
      "companyName": "Bio Medica Laboratories Limited",
      "symbol": "BMLL",
      "series": "SME",
      "isBse": "1",
      "issueStartDate": "21-May-2026",
      "issueEndDate": "25-May-2026",
      "status": "Active",
      "noOfSharesOffered": "3772000",
      "noOfsharesBid": "3263000",
      "noOfTime": "0.87"
    }

We don't have ISIN or PAN here, so the stable join key falls back to
``name_year``. Multi-source merge (`subscription.times_x` may come from
BSE too) happens in the pipeline's precedence step.
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


# Map raw NSE status strings to canonical enum values defined in
# docs/schema/v2/issue.schema.json identity.status.
_STATUS_MAP = {
    "active": "Open",
    "open": "Open",
    "closed": "Closed",
    "listed": "Listed",
    "withdrawn": "Withdrawn",
    "upcoming": "Upcoming",
    "forthcoming": "Upcoming",
}


@register_parser("nse", "ipo_current_issue")
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
    company_name = (row.get("companyName") or "").strip()
    if not company_name:
        return None

    open_date = _safe_date(row.get("issueStartDate"))
    close_date = _safe_date(row.get("issueEndDate"))

    listing_year = open_date.year if open_date else None
    if listing_year is None:
        # No date → cannot construct a stable name_year join key (E.ID.001).
        # Drop the row; the next snapshot (with dates filled in) will cover it.
        return None

    identity = build_identity(
        company_name=company_name,
        isin=None,
        pan=None,
        listing_year=listing_year,
    )
    join_key = IssueJoinKey(
        discriminator=identity.stable_join_key.split(":", 1)[0],
        value=identity.stable_join_key.split(":", 1)[1],
    )

    fields: dict[str, Any] = {
        "identity.company_name": company_name,
        "identity.slug": identity.slug,
        "identity.symbol": (row.get("symbol") or None) or None,
        "identity.board_type": _board_type(row.get("series")),
        "identity.status": _status(row.get("status")),
        "identity.issue_type": "IPO",
        "timeline.open_date": open_date.isoformat() if open_date else None,
        "timeline.close_date": close_date.isoformat() if close_date else None,
        "pricing.issue_size_shares": _safe_int(row.get("noOfSharesOffered")),
        "subscription.overall_times_x": _safe_times(row.get("noOfTime")),
        # isBse=='1' indicates BSE cross-listing — recorded in aliases for
        # the merge step (E.ID.006).
        "identity.aliases": _aliases(row),
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
    if dec is None:
        return None
    return str(dec)  # canonical: Decimal serialized as string ("_x" suffix)


def _board_type(series: Any) -> str | None:
    text = str(series or "").strip().upper()
    if text == "SME":
        return "SME Board"
    if text in ("EQ", "BE", "B1", "B2", ""):
        return "Main Board"
    return None


def _status(raw: Any) -> str | None:
    text = str(raw or "").strip().lower()
    return _STATUS_MAP.get(text)


def _aliases(row: dict[str, Any]) -> list[str] | None:
    aliases: list[str] = []
    is_bse = _try_coerce_bool(row.get("isBse"))
    symbol = row.get("symbol")
    if symbol and is_bse:
        aliases.append(f"bse:cross_listed:{symbol}")
    return aliases or None


def _try_coerce_bool(value: Any) -> bool | None:
    try:
        return coerce_bool(value)
    except UnitParseError:
        return None
