"""Parse NSE OFS (Offer For Sale) endpoints.

Catalogs (5 endpoints, identical row shape):
* ``ofs_past`` (436 historical OFS rows)
* ``ofs_past_general`` (category-filtered: GENERAL retail/HNI split)
* ``ofs_past_retail``
* ``ofs_active_grouped`` (currently active OFS)
* ``ofs_forthcoming``

Sample row::

    {
      "sr_no": 1,
      "symbol": "EASTSILKCUMU",
      "companyName": "Eastern Silk Industries Limited",
      "category": "GENERAL",
      "offerDate": "19-Mar-2026",
      "floorPrice": "63",                  // ₹ per share
      "noOfshareOffered": "39263",
      "allocatePrice": "-",                // populated post-allotment
      "noOfTimes": "       .00",           // subscription multiple
      "methodology": "-",
      "allocatedQty": "-"
    }

The response is wrapped: ``{"data": [rows...]}``.

OFS is a separate issue type from IPO (sale of *existing* shares by
existing shareholders, not new issuance — see EDGE_CASES E.STA.002).
"""

from __future__ import annotations

from typing import Any

from ...normalization import (
    UnitParseError,
    coerce_int,
    parse_indian_date,
    parse_monetary_to_paise,
    parse_subscription_multiple,
    sanitize_plaintext,
)
from ..identity import build_identity
from ..pipeline import Contribution, IssueJoinKey
from .registry import ParserContext, register_parser


_ACTIVE_FORTHCOMING_STATUS = {
    "ofs_active_grouped": "Open",
    "ofs_active_general": "Open",
    "ofs_active_retail": "Open",
    "ofs_active_total_retail": "Open",
    "ofs_forthcoming": "Upcoming",
}


@register_parser("nse", "ofs_past")
@register_parser("nse", "ofs_past_general")
@register_parser("nse", "ofs_past_retail")
@register_parser("nse", "ofs_active_grouped")
@register_parser("nse", "ofs_active_general")
@register_parser("nse", "ofs_active_retail")
@register_parser("nse", "ofs_active_total_retail")
@register_parser("nse", "ofs_forthcoming")
def parse(body: Any, ctx: ParserContext) -> list[Contribution]:
    rows = _extract_rows(body)
    out: list[Contribution] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        contribution = _row_to_contribution(row, ctx)
        if contribution is not None:
            out.append(contribution)
    return out


def _extract_rows(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, dict):
        data = body.get("data") or []
        if isinstance(data, list):
            return data
    if isinstance(body, list):
        return body
    return []


def _row_to_contribution(row: dict[str, Any], ctx: ParserContext) -> Contribution | None:
    company_name = sanitize_plaintext(row.get("companyName") or row.get("company"))
    if not company_name:
        return None

    offer_date = _safe_date(row.get("offerDate"))
    end_date = _safe_date(row.get("offerEndDate"), anchor_year=offer_date.year if offer_date else None)
    listing_year = (offer_date or end_date).year if (offer_date or end_date) else None
    if listing_year is None:
        return None

    identity = build_identity(
        company_name=company_name,
        listing_year=listing_year,
    )
    discriminator, _, value = identity.stable_join_key.partition(":")
    join_key = IssueJoinKey(discriminator=discriminator, value=value)

    floor_paise = _safe_money(row.get("floorPrice"))
    allocate_paise = _safe_money(row.get("allocatePrice"))
    shares = _safe_int(row.get("noOfshareOffered"))
    times = _safe_times(row.get("noOfTimes"))

    status = _ACTIVE_FORTHCOMING_STATUS.get(ctx.endpoint, "Closed")
    if allocate_paise:
        status = "Listed"  # OFS settled / fully allocated.

    fields: dict[str, Any] = {
        "identity.company_name": company_name,
        "identity.slug": identity.slug,
        "identity.symbol": _safe_symbol(row.get("symbol")),
        "identity.status": status,
        "identity.issue_type": "OFS",
        "timeline.open_date": offer_date.isoformat() if offer_date else None,
        "timeline.close_date": end_date.isoformat() if end_date else offer_date.isoformat() if offer_date else None,
        # Allocated price wins over floor; absence of allocated means
        # the floor is what's published.
        "pricing.issue_price_paise": allocate_paise or floor_paise,
        "pricing.price_band_lower_paise": floor_paise,
        "pricing.issue_size_shares": shares,
        "subscription.overall_times_x": times,
    }
    fields = {k: v for k, v in fields.items() if v is not None}

    return Contribution(
        source=ctx.source,
        endpoint=ctx.endpoint,
        snapshot_at=ctx.snapshot_at,
        join_key=join_key,
        fields=fields,
    )


def _safe_date(value: Any, anchor_year: int | None = None):
    text = sanitize_plaintext(value)
    if not text:
        return None
    try:
        return parse_indian_date(text, anchor_year=anchor_year)
    except UnitParseError:
        return None


def _safe_int(value: Any):
    text = sanitize_plaintext(value)
    if text in (None, "-"):
        return None
    try:
        return coerce_int(text)
    except UnitParseError:
        return None


def _safe_money(value: Any):
    text = sanitize_plaintext(value)
    if not text or text == "-":
        return None
    try:
        return parse_monetary_to_paise(text)
    except UnitParseError:
        return None


def _safe_times(value: Any):
    text = sanitize_plaintext(value)
    if not text or text == "-":
        return None
    try:
        dec = parse_subscription_multiple(text)
    except UnitParseError:
        return None
    return None if dec is None else str(dec)


def _safe_symbol(value: Any) -> str | None:
    text = sanitize_plaintext(value)
    if not text or text == "-":
        return None
    return text
