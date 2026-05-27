"""Parse BSE IPO document index into canonical document URLs."""

from __future__ import annotations

from datetime import date
from typing import Any
from urllib.parse import quote

from ...normalization import UnitParseError, clean_text, parse_indian_date, sanitize_plaintext
from ..identity import build_identity
from ..pipeline import Contribution, IssueJoinKey
from .registry import ParserContext, register_parser


BSE_IPO_DOWNLOAD_BASE = "https://www.bseindia.com/downloads/ipo/"


@register_parser("bse", "ipo_documents")
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
        rows = body.get("table") or body.get("Table") or []
        if isinstance(rows, list):
            return rows
    if isinstance(body, list):
        return body
    return []


def _row_to_contribution(row: dict[str, Any], ctx: ParserContext) -> Contribution | None:
    company = sanitize_plaintext(row.get("Scrip_Name"))
    if not company:
        return None
    anchor = _updated_date(row.get("updated_date")) or _ctx_date(ctx.snapshot_at)
    if anchor is None:
        return None

    identity = build_identity(company_name=company, listing_year=anchor.year)
    discriminator, _, value = identity.stable_join_key.partition(":")
    join_key = IssueJoinKey(discriminator=discriminator, value=value)

    fields: dict[str, Any] = {
        "identity.company_name": company,
        "identity.slug": identity.slug,
        "identity.issue_type": "IPO",
        "identity.status": "Filed",
        "identity.aliases": _aliases(row),
        "documents.drhp_url": _doc_url(row.get("DRHP_Doc")),
        "documents.rhp_url": _doc_url(row.get("Red_Herring_Prospectus")),
        "documents.prospectus_url": _doc_url(row.get("Prospectus")),
        "documents.basis_allotment_url": _doc_url(row.get("T5Stage_Document")),
    }
    fields = {k: v for k, v in fields.items() if v is not None}
    return Contribution(
        source=ctx.source,
        endpoint=ctx.endpoint,
        snapshot_at=ctx.snapshot_at,
        join_key=join_key,
        fields=fields,
    )


def _doc_url(value: Any) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    if text.startswith(("http://", "https://")):
        return text
    return f"{BSE_IPO_DOWNLOAD_BASE}{quote(text, safe='/')}"


def _aliases(row: dict[str, Any]) -> list[str] | None:
    aliases: list[str] = []
    scrip_cd = clean_text(row.get("scrip_cd"))
    prior_id = clean_text(row.get("Prior_Id"))
    created_by = clean_text(row.get("createdby"))
    if scrip_cd:
        aliases.append(f"bse:scrip_code:{scrip_cd}")
    if prior_id and prior_id != "0":
        aliases.append(f"bse:prior_id:{prior_id}")
    if created_by and created_by != "0":
        aliases.append(f"bse:createdby:{created_by}")
    return aliases or None


def _updated_date(value: Any) -> date | None:
    text = clean_text(value)
    if not text:
        return None
    date_part = text.split(" ", 1)[0]
    parts = date_part.split("/")
    if len(parts) == 3:
        # BSE emits M/D/YYYY here, unlike the normal Indian date feeds.
        try:
            month, day, year = (int(p) for p in parts)
            return date(year, month, day)
        except ValueError:
            return None
    try:
        return parse_indian_date(date_part)
    except UnitParseError:
        return None


def _ctx_date(value: str):
    try:
        return parse_indian_date(value[:10])
    except UnitParseError:
        return None
