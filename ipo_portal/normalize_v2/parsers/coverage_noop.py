"""Register intentionally non-canonical helper feeds.

These endpoints are dropdown/statistical helpers for other primary pages. They
are fetched so drift can be observed, but they do not by themselves carry enough
issue-level facts to publish canonical records without creating duplicates.
"""

from __future__ import annotations

from typing import Any

from .registry import ParserContext, register_parser


@register_parser("nse", "offer_documents_equity_companylist")
@register_parser("nse", "offer_documents_sme_companylist")
@register_parser("nse", "public_issue_company_list")
@register_parser("bse", "ipo_years")
@register_parser("bse", "ipo_tracker_current_year")
def parse(_body: Any, _ctx: ParserContext) -> list:
    return []
