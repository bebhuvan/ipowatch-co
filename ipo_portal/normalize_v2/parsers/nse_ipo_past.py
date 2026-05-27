"""Parse the NSE ``public-past-issues`` JSON list (~380KB payload).

Catalog reference: ``docs/schema/raw_catalog/nse/ipo_public_past_issues.json``.

Sample row::

    {
      "symbol": "YATRA",
      "companyName": "Yatra Online Limited",
      "ipoStartDate": "15-Sep-2023",
      "ipoEndDate": "20-Sep-2023",
      "listingDate": "28-Sep-2023",
      "issuePrice": "142",
      "priceRange": "135 - 142",
      "securityType": "EQ"
    }

Numbers come as strings (`E.NUM.003`). `issuePrice` is rupees not paise;
the normalizer's ``parse_monetary_to_paise`` handles the conversion at
the boundary.
"""

from __future__ import annotations

from typing import Any

from ...normalization import (
    UnitParseError,
    coerce_int,
    parse_indian_date,
    parse_monetary_to_paise,
)
from ..identity import build_identity
from ..pipeline import Contribution, IssueJoinKey
from .registry import ParserContext, register_parser


# Map NSE securityType → canonical board_type. SME and SME-tagged variants
# go to "SME Board"; EQ / BE / blank / null go to "Main Board". Debt and
# other non-equity types fall to "Other" (we leave board_type null so the
# v2 record's classification block can surface them via issue_type).
_SECURITY_TYPE_TO_BOARD = {
    "EQ": "Main Board",
    "BE": "Main Board",
    "B1": "Main Board",
    "B2": "Main Board",
    "SME": "SME Board",
}


# InvIT (`securityType=IV`) and REIT (`securityType=RR`) endpoints publish
# the same row shape, so the parser is registered for them too. The
# infer_issue_type() helper maps each securityType to the canonical
# issue_type enum.
@register_parser("nse", "ipo_public_past_issues")
@register_parser("nse", "ipo_past_security_type")
@register_parser("nse", "invits_past")
@register_parser("nse", "invits_current")
@register_parser("nse", "reits_past")
@register_parser("nse", "reits_current")
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

    open_date = _safe_date(row.get("ipoStartDate"))
    # Anchor close + listing years to the open year — repairs source typos
    # like "0202-02-07" → "2022-02-07" (E.DAT).
    _anchor = open_date.year if open_date else None
    close_date = _safe_date(row.get("ipoEndDate"), anchor_year=_anchor)
    listing_date = _safe_date(row.get("listingDate"), anchor_year=_anchor)
    listing_before_offer = bool(listing_date and open_date and listing_date < open_date)
    if listing_before_offer:
        listing_date = None

    # Past issues are anchored by the listing year — that's what aligns
    # the NSE row to the BSE row to the PRIME row for the same canonical
    # issue. If listing is missing we fall back to open date.
    anchor_date = listing_date or open_date or close_date
    if anchor_date is None:
        return None

    identity = build_identity(
        company_name=company_name,
        listing_year=anchor_date.year,
    )
    discriminator, _, value = identity.stable_join_key.partition(":")
    join_key = IssueJoinKey(discriminator=discriminator, value=value)

    security_type = (row.get("securityType") or "").strip().upper()
    if security_type == "GB":
        return None
    board_type = _SECURITY_TYPE_TO_BOARD.get(security_type)

    # priceRange is e.g. "135 - 142" → lower 13500 paise, upper 14200 paise.
    price_band_lower_paise, price_band_upper_paise = _parse_price_range(row.get("priceRange"))
    issue_price_paise = _safe_money(row.get("issuePrice"))
    if (
        issue_price_paise is not None
        and price_band_lower_paise is not None
        and price_band_upper_paise is not None
        and not (price_band_lower_paise * 0.99 <= issue_price_paise <= price_band_upper_paise * 1.01)
    ):
        issue_price_paise = None

    fields: dict[str, Any] = {
        "identity.company_name": company_name,
        "identity.slug": identity.slug,
        "identity.symbol": (row.get("symbol") or None) or None,
        "identity.board_type": board_type,
        "identity.status": "Listed" if listing_date else "Closed",
        "identity.issue_type": "FPO" if listing_before_offer else _infer_issue_type(security_type),
        "timeline.open_date": open_date.isoformat() if open_date else None,
        "timeline.close_date": close_date.isoformat() if close_date else None,
        "timeline.listing_date": listing_date.isoformat() if listing_date else None,
        "pricing.issue_price_paise": issue_price_paise,
        "pricing.price_band_lower_paise": price_band_lower_paise,
        "pricing.price_band_upper_paise": price_band_upper_paise,
        "pricing.issue_size_shares": _safe_int(row.get("noOfSharesOffered")),
    }
    fields = {k: v for k, v in fields.items() if v is not None}

    return Contribution(
        source=ctx.source,
        endpoint=ctx.endpoint,
        snapshot_at=ctx.snapshot_at,
        join_key=join_key,
        fields=fields,
    )


def _parse_price_range(value: Any) -> tuple[int | None, int | None]:
    """Parse strings like ``"135 - 142"`` into ``(lower_paise, upper_paise)``."""
    if not value or not isinstance(value, str):
        return None, None
    normalized = value.replace("–", "-")
    normalized = normalized.replace(" to ", "-").replace(" To ", "-").replace(" TO ", "-")
    parts = [p.strip() for p in normalized.split("-")]
    if len(parts) != 2:
        return None, None
    try:
        lower = parse_monetary_to_paise(parts[0])
        upper = parse_monetary_to_paise(parts[1])
    except UnitParseError:
        return None, None
    return lower, upper


def _safe_money(value: Any) -> int | None:
    if not value:
        return None
    try:
        return parse_monetary_to_paise(value)
    except UnitParseError:
        return None


def _safe_date(value: Any, anchor_year: int | None = None):
    try:
        return parse_indian_date(value, anchor_year=anchor_year)
    except UnitParseError:
        return None


def _safe_int(value: Any):
    try:
        return coerce_int(value)
    except UnitParseError:
        return None


def _infer_issue_type(security_type: str) -> str:
    """Map NSE securityType → canonical issue_type enum.

    Enum: IPO | FPO | Rights | Buyback | OFS | NCD | InvIT | REIT | Others.
    """
    s = security_type.upper()
    if s in ("EQ", "BE", "B1", "B2", "SME", ""):
        return "IPO"
    if s in ("N0", "NCD", "DEBT", "NCB"):
        return "NCD"  # public debt / non-convertible debenture issue
    if s == "IV":
        return "InvIT"
    if s == "RR":
        return "REIT"
    return "Others"
