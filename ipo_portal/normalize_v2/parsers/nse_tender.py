"""Parse NSE Tender Offer endpoints (buybacks and takeover offers).

Catalogs (3 endpoints, identical shape):
* ``tender_active``
* ``tender_forthcoming``
* ``tender_past`` (207 historical tender rows)

Sample row::

    {
      "sr_no": 1,
      "symbol": "emapartner",
      "company": "EMA Partners India Limited",
      "offerType": "Buy back",
      "issueType": "Fixed price",
      "series": "BB",
      "band": "100",                     // single price for fixed-price
      "allocatedPrice": "100",
      "issueSize": "725000",             // shares
      "allocatedDmQty": "725000",
      "allocatedPhyQty": "0",
      "cumQtyDmat": "2180000",
      "totQty": "2180000",
      "numberOfTimes": "      3.01",
      "offerDate": "07-May-2026",
      "offerEndDate": "13-May-2026"
    }

NSE tender includes both **buybacks** (offerType="Buy back") and
**takeover offers** (offerType="Open Offer"). The canonical
``issue_type`` enum maps:

* ``Buy back``           → ``Buyback``
* ``Open Offer``         → ``Others``  (we don't have a Takeover enum yet)
* ``Delisting``          → ``Others``
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


_OFFER_TYPE_TO_ISSUE_TYPE = {
    "BUY BACK": "Buyback",
    "BUYBACK": "Buyback",
    "OPEN OFFER": "Others",
    "DELISTING": "Others",
}

_STATUS_BY_ENDPOINT = {
    "tender_active": "Open",
    "tender_forthcoming": "Upcoming",
    "tender_past": "Closed",
}


@register_parser("nse", "tender_active")
@register_parser("nse", "tender_forthcoming")
@register_parser("nse", "tender_past")
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
    company = sanitize_plaintext(row.get("company") or row.get("companyName"))
    if not company:
        return None
    offer_date = _safe_date(row.get("offerDate"))
    end_date = _safe_date(row.get("offerEndDate"), anchor_year=offer_date.year if offer_date else None)
    anchor = offer_date or end_date
    if anchor is None:
        return None

    identity = build_identity(company_name=company, listing_year=anchor.year)
    discriminator, _, value = identity.stable_join_key.partition(":")
    join_key = IssueJoinKey(discriminator=discriminator, value=value)

    offer_type_raw = (row.get("offerType") or "").strip().upper()
    issue_type = _OFFER_TYPE_TO_ISSUE_TYPE.get(offer_type_raw, "Buyback")

    band_paise = _safe_money(row.get("band"))
    allocated_price_paise = _safe_money(row.get("allocatedPrice"))
    issue_size_shares = _safe_int(row.get("issueSize"))
    times = _safe_times(row.get("numberOfTimes"))

    fields: dict[str, Any] = {
        "identity.company_name": company,
        "identity.slug": identity.slug,
        "identity.symbol": _safe_text(row.get("symbol")),
        "identity.status": _STATUS_BY_ENDPOINT.get(ctx.endpoint, "Closed"),
        "identity.issue_type": issue_type,
        "timeline.open_date": offer_date.isoformat() if offer_date else None,
        "timeline.close_date": end_date.isoformat() if end_date else None,
        # For fixed-price buybacks the "band" is a single price.
        "pricing.issue_price_paise": allocated_price_paise or band_paise,
        "pricing.price_band_lower_paise": band_paise,
        "pricing.price_band_upper_paise": band_paise,
        "pricing.issue_size_shares": issue_size_shares,
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


def _safe_text(value: Any) -> str | None:
    text = sanitize_plaintext(value)
    if not text or text == "-":
        return None
    return text
