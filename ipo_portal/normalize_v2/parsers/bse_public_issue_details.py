"""Parse BSE ``GetPublicIssue_par/w`` (public issue details).

Catalog reference: ``docs/schema/raw_catalog/bse/public_issue_details.json``.

The BSE response is a dict ``{"Table": [rows...]}`` where each row is a
single issue with fields:

* ``IPO_NO``        — BSE-internal IPO sequence number (joins to per-issue feeds).
* ``Scrip_cd``      — BSE Scrip Code (numeric).
* ``Scrip_Name``    — Short ticker name (max ~16 chars).
* ``LONG_NAME``     — Full legal company name.
* ``Start_Dt``, ``End_Dt`` — ISO timestamps (with time, IST).
* ``Status``        — Single char: "L" listed, "F" forthcoming, "O" open, etc.
* ``IR_flag`` / ``IR_FLAG_FULL`` — Issue type (IPO/OFS/Rights/Buyback/etc.).
* ``Price_Band``    — Free-text band, e.g. ``"135-142"`` or ``"100"`` (flat).
* ``Face_Val``      — Per-share face value in rupees (typically 10 or 1).
* ``eXCHANGE_PLATFORM`` — "Main Board" or "SME Platform" or null.

BSE handles HTML in some fields (`E.HTM.001`); we sanitize.
"""

from __future__ import annotations

import re
from typing import Any

from ...normalization import (
    UnitParseError,
    parse_indian_date,
    parse_indian_instant,
    parse_monetary_to_paise,
    sanitize_plaintext,
)
from ..identity import build_identity
from ..pipeline import Contribution, IssueJoinKey
from .registry import ParserContext, register_parser


_STATUS_MAP = {
    "L": "Open",
    "F": "Upcoming",
    "O": "Open",
    "C": "Closed",
    "W": "Withdrawn",
}

_IR_FLAG_TO_ISSUE_TYPE = {
    "IPO": "IPO",
    "FPO": "FPO",
    "OFS": "OFS",
    "RI": "Rights",
    "RIGHTS": "Rights",
    "RI/FPO": "Rights",
    "BB": "Buyback",
    "BUYBACK": "Buyback",
    "OTB": "Buyback",   # Offer To Buy-back (BSE's buyback-tender flag)
    "DPI": "NCD",       # Debt Public Issue (public NCD float)
    "DELIST": "Others",
    "TENDER": "Others",
    "CMN": "Call Money",  # call-money / partly-paid equity follow-on call
}

# Price-band parsing — accept "135-142", "135 to 142", "100", "Rs. 135–142".
_BAND_SPLIT_RE = re.compile(r"\s*(?:-|–|—|to|TO)\s*")


@register_parser("bse", "public_issue_details")
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


@register_parser("bse", "public_issue")
def parse_public_issue(body: Any, ctx: ParserContext) -> list[Contribution]:
    """The non-`_par` variant has the same row shape; reuse the same parser."""
    return parse(body, ctx)


def _extract_rows(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, dict):
        rows = body.get("Table") or body.get("data") or []
        if isinstance(rows, list):
            return rows
    if isinstance(body, list):
        return body
    return []


def _row_to_contribution(row: dict[str, Any], ctx: ParserContext) -> Contribution | None:
    long_name = sanitize_plaintext(row.get("LONG_NAME") or row.get("Scrip_Name"))
    if not long_name:
        return None

    open_date = _safe_date(row.get("Start_Dt"))
    close_date = _safe_date(row.get("End_Dt"))

    listing_year = (open_date or close_date).year if (open_date or close_date) else None
    if listing_year is None:
        return None

    identity = build_identity(
        company_name=long_name,
        listing_year=listing_year,
    )
    discriminator, _, value = identity.stable_join_key.partition(":")
    join_key = IssueJoinKey(discriminator=discriminator, value=value)

    ir_flag = (row.get("IR_flag") or row.get("IR_FLAG_FULL") or "").strip().upper()
    issue_type = _IR_FLAG_TO_ISSUE_TYPE.get(ir_flag, "Others")
    status = _STATUS_MAP.get((row.get("Status") or "").strip().upper(), None)

    band_lower_paise, band_upper_paise = _parse_band(row.get("Price_Band"))
    face_value_paise = _safe_money(row.get("Face_Val"))

    board_type = _board_from_platform(row.get("eXCHANGE_PLATFORM"))

    aliases: list[str] = []
    ipo_no = row.get("IPO_NO")
    scrip_cd = row.get("Scrip_cd")
    if ipo_no:
        aliases.append(f"bse:ipo_no:{ipo_no}")
    if scrip_cd:
        aliases.append(f"bse:scrip_code:{scrip_cd}")

    fields: dict[str, Any] = {
        "identity.company_name": long_name,
        "identity.slug": identity.slug,
        "identity.symbol": _short_ticker(row),
        "identity.board_type": board_type,
        "identity.status": status,
        "identity.issue_type": issue_type,
        "timeline.open_date": open_date.isoformat() if open_date else None,
        "timeline.close_date": close_date.isoformat() if close_date else None,
        "pricing.price_band_lower_paise": band_lower_paise,
        "pricing.price_band_upper_paise": band_upper_paise,
        "pricing.face_value_paise": face_value_paise,
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


def _short_ticker(row: dict[str, Any]) -> str | None:
    candidate = row.get("short_name") or row.get("Scrip_Name")
    text = sanitize_plaintext(candidate)
    if not text:
        return None
    # Scrip_Name is sometimes the long name; require a tickerish length.
    if len(text) > 20:
        return None
    return text


def _parse_band(value: Any) -> tuple[int | None, int | None]:
    text = sanitize_plaintext(value)
    if not text:
        return None, None
    # BSE sometimes prefixes with "Rs." or "₹"; the monetary parser strips.
    parts = _BAND_SPLIT_RE.split(text, maxsplit=1)
    if len(parts) == 1:
        # Flat-price band, e.g. buybacks at one price.
        price = _safe_money(parts[0])
        return price, price
    try:
        lower = parse_monetary_to_paise(parts[0])
        upper = parse_monetary_to_paise(parts[1])
    except UnitParseError:
        return None, None
    return lower, upper


def _safe_money(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        paise = parse_monetary_to_paise(value)
    except UnitParseError:
        return None
    # BSE uses "0.00" as a placeholder lower band for fixed-price issues.
    return paise or None


def _safe_instant(value: Any):
    try:
        return parse_indian_instant(value)
    except UnitParseError:
        return None


def _safe_date(value: Any):
    text = sanitize_plaintext(value)
    if not text:
        return None
    try:
        return parse_indian_date(text[:10])
    except UnitParseError:
        return None


def _board_from_platform(value: Any) -> str | None:
    text = sanitize_plaintext(value)
    if not text:
        return None
    upper = text.upper()
    if "SME" in upper:
        return "SME Board"
    if "MAIN" in upper:
        return "Main Board"
    return None
