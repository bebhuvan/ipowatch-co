"""Parse NSE Rights issue endpoints.

Catalogs (3 endpoints, identical shape):
* ``rights_active``
* ``rights_forthcoming``
* ``rights_past`` (330 historical rights rows)

Sample row::

    {
      "sr_no": 1,
      "symbol": "SILGOFCM",
      "company": "Silgo Retail Limited - Call Money",
      "rightStartDate": "24-Apr-2026",
      "rightEndDate": "08-May-2026",
      "bidQty": null,
      "nse_bse_cumu": null
    }

Sparse data — NSE's rights feed is mostly dates + company + symbol.
The richer detail (price, ratio, premium) lives in the BSE
``rights_issue_documents`` feed (separate parser).

Rights are a separate issue type from IPO. Company-name "- Call Money"
suffixes mark call-money tranches of rights; we keep them as separate
records (each tranche has its own dates).
"""

from __future__ import annotations

from typing import Any

from ...normalization import (
    UnitParseError,
    coerce_int,
    parse_indian_date,
    sanitize_plaintext,
)
from ..identity import build_identity
from ..pipeline import Contribution, IssueJoinKey
from .registry import ParserContext, register_parser


_STATUS_BY_ENDPOINT = {
    "rights_active": "Open",
    "rights_forthcoming": "Upcoming",
    "rights_past": "Closed",
}


@register_parser("nse", "rights_active")
@register_parser("nse", "rights_forthcoming")
@register_parser("nse", "rights_past")
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
    start_date = _safe_date(row.get("rightStartDate") or row.get("startDate"))
    # Anchor the end date's year to the start year — repairs source typos
    # like "0202-02-07" → "2022-02-07" (E.DAT).
    end_date = _safe_date(
        row.get("rightEndDate") or row.get("endDate"),
        anchor_year=start_date.year if start_date else None,
    )
    anchor = start_date or end_date
    if anchor is None:
        return None

    identity = build_identity(company_name=company, listing_year=anchor.year)
    discriminator, _, value = identity.stable_join_key.partition(":")
    join_key = IssueJoinKey(discriminator=discriminator, value=value)

    fields: dict[str, Any] = {
        "identity.company_name": company,
        "identity.slug": identity.slug,
        "identity.symbol": _safe_text(row.get("symbol")),
        "identity.status": _STATUS_BY_ENDPOINT.get(ctx.endpoint, "Closed"),
        "identity.issue_type": "Rights",
        "timeline.open_date": start_date.isoformat() if start_date else None,
        "timeline.close_date": end_date.isoformat() if end_date else None,
        "subscription.overall_times_x": None,  # NSE rights feed lacks this
        "pricing.issue_size_shares": _safe_int(row.get("bidQty")),
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


def _safe_text(value: Any) -> str | None:
    text = sanitize_plaintext(value)
    if not text or text == "-":
        return None
    return text
