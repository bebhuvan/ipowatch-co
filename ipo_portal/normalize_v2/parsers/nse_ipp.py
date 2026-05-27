"""Parse NSE IPP (Institutional Placement Programme) endpoints.

Catalogs (3 endpoints):
* ``ipp_active``
* ``ipp_forthcoming``
* ``ipp_past`` (17 historical rows)

Sample row::

    {
      "autoIncrement": 1,
      "symbol": "INDIGO",
      "company": "InterGlobe Aviation Limited",
      "ippStartDate": "15-Sep-2017",
      "ippEndDate": "15-Sep-2017",
      "issueSize": "33578421",     // shares
      "ieqQty": "38949027",
      "timeStamp": null
    }

IPP is a SEBI-defined route for institutional equity placements. Rare
(17 rows over many years). Mapped to ``issue_type=Others`` since the
canonical enum doesn't have a dedicated IPP value.
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
    "ipp_active": "Open",
    "ipp_forthcoming": "Upcoming",
    "ipp_past": "Closed",
}


@register_parser("nse", "ipp_active")
@register_parser("nse", "ipp_forthcoming")
@register_parser("nse", "ipp_past")
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
    start_date = _safe_date(row.get("ippStartDate"))
    end_date = _safe_date(row.get("ippEndDate"))
    anchor = start_date or end_date
    if anchor is None:
        return None

    identity = build_identity(company_name=company, listing_year=anchor.year)
    discriminator, _, value = identity.stable_join_key.partition(":")
    join_key = IssueJoinKey(discriminator=discriminator, value=value)

    fields: dict[str, Any] = {
        "identity.company_name": company,
        "identity.slug": identity.slug,
        "identity.symbol": sanitize_plaintext(row.get("symbol")),
        "identity.status": _STATUS_BY_ENDPOINT.get(ctx.endpoint, "Closed"),
        "identity.issue_type": "Others",  # IPP isn't in the canonical enum yet
        "timeline.open_date": start_date.isoformat() if start_date else None,
        "timeline.close_date": end_date.isoformat() if end_date else None,
        "pricing.issue_size_shares": _safe_int(row.get("issueSize")),
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
    text = sanitize_plaintext(value)
    if not text:
        return None
    try:
        return parse_indian_date(text)
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
