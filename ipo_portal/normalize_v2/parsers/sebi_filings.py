"""Parse SEBI public-issue filings into v2 contributions.

Source snapshot: ``data/raw/sebi/public_issue_filings/<ts>.json`` written
by ``ipo_portal.sebi.scrape``. Body is a list of filing dicts:

    {
      "filing_date": "2026-05-20",
      "company_name": "M. K. SONS FINE JEWELS LIMITED",
      "detail_url": "https://www.sebi.gov.in/filings/.../..._101528.html",
      "document_url": "https://www.sebi.gov.in/sebi_data/attachdocs/may-2026/..._1256.pdf",
      "document_type": "DRHP"
    }

SEBI is the earliest signal — a DRHP filing precedes the NSE/BSE
listing. We emit a ``Filed``-status contribution carrying the DRHP URL
and filing date. The consolidation pass later unions this into the
eventual live/listed record for the same company (same normalized name,
within 2 years), so the SEBI DRHP URL lands on the canonical issue.

Tier: ``primary`` for the filing existence + DRHP URL; SEBI is the
regulator of record. It does not carry pricing/subscription, so those
come from NSE/BSE.
"""

from __future__ import annotations

from typing import Any

from ...normalization import UnitParseError, parse_indian_date, sanitize_plaintext
from ..identity import build_identity
from ..pipeline import Contribution, IssueJoinKey
from .registry import ParserContext, register_parser


@register_parser("sebi", "public_issue_filings")
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
    company = sanitize_plaintext(row.get("company_name"))
    if not company:
        return None
    filing_date = _safe_date(row.get("filing_date"))
    # Filing year anchors the join so the DRHP unions to the eventual
    # listed record (consolidation allows ±2 years).
    year = filing_date.year if filing_date else None
    if year is None:
        return None

    identity = build_identity(company_name=company, listing_year=year)
    discriminator, _, value = identity.stable_join_key.partition(":")
    join_key = IssueJoinKey(discriminator=discriminator, value=value)

    doc_url = row.get("document_url")
    doc_type = (row.get("document_type") or "").upper()

    fields: dict[str, Any] = {
        "identity.company_name": company,
        "identity.slug": identity.slug,
        "identity.issue_type": "IPO",
        # No status set here — _infer_status resolves "Filed" because a
        # document is present and there's no timeline. If NSE/BSE later
        # report a listing, their primary status wins post-consolidation.
        "timeline.drhp_filing_date": filing_date.isoformat() if filing_date else None,
        "identity.aliases": [f"sebi:detail:{row.get('detail_url')}"] if row.get("detail_url") else None,
    }
    # Route the SEBI PDF to the right document slot.
    if doc_url:
        if "UDRHP" in doc_type or "DRHP" in doc_type or "DRAFT" in doc_type:
            fields["documents.drhp_url"] = doc_url
        elif "ABRIDGED" in doc_type:
            fields["documents.prospectus_url"] = doc_url
        else:
            fields["documents.drhp_url"] = doc_url

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
