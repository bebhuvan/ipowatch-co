"""Register BSE primary-action document feeds for explicit coverage.

These feeds are primary BSE surfaces for documents/status XBRL around bonds,
buybacks, QIPs, rights, InvIT/REIT placements, takeovers, and delistings. The
current V3 public issue schema has only a small set of IPO document URL fields,
so this parser intentionally does not coerce XBRL phase documents into the
wrong IPO fields. Rich per-action modules should be added as separate schemas.
"""

from __future__ import annotations

from typing import Any

from .registry import ParserContext, register_parser


@register_parser("bse", "bond_issue_documents")
@register_parser("bse", "buyback_open_market_documents")
@register_parser("bse", "buyback_tender_documents")
@register_parser("bse", "invit_placement_documents")
@register_parser("bse", "invit_reit_documents")
@register_parser("bse", "qip_documents")
@register_parser("bse", "rights_issue_documents")
@register_parser("bse", "takeover_documents")
@register_parser("bse", "voluntary_delisting_documents")
def parse(_body: Any, _ctx: ParserContext) -> list:
    return []
