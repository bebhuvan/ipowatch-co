"""Per-source row parsers for the v2 normalizer.

Each module here registers one or more parsers via the
``register_parser`` decorator. The pipeline's
``V2Pipeline.collect_contributions`` walks raw snapshots and dispatches
to the matching parser by ``(source, endpoint)`` family.

A parser converts a raw snapshot into a list of ``Contribution``
objects. Each contribution carries:

* A stable ``IssueJoinKey`` (built via ``identity.stable_join_key``).
* A dotted-path → coerced-value map under ``.fields`` matching the v2
  canonical schema.

Parsers do not write to disk; the pipeline aggregates contributions by
join key, applies precedence, validates, and writes.
"""

from __future__ import annotations

from .registry import (
    PARSERS,
    ParserContext,
    ParserResult,
    register_parser,
    parser_for,
)
from . import (  # noqa: F401  — registers parsers
    bse_ipo_documents,
    bse_demand_schedule,
    bse_bid_summary,
    bse_ipo_performance,
    bse_issue_detail,
    bse_primary_document_feeds,
    bse_public_issue_details,
    coverage_noop,
    kite_performance,
    moneycontrol_listed,
    nse_ipo_current,
    nse_ipo_past,
    nse_bid_summary,
    nse_demand_data,
    nse_ipo_upcoming,
    nse_ipp,
    nse_ofs,
    nse_offer_documents,
    nse_offer_document_details,
    nse_public_issue_advertisements,
    nse_rights,
    nse_tender,
    sebi_filings,
    yahoo_performance,
)

def register_concrete_endpoints() -> None:
    """Re-scan data/raw and register suffix-keyed concrete endpoints.

    Parser modules register concrete names (``issue_detail_7722``,
    ``listed_ipos_page_000020``, …) by scanning ``data/raw`` at import
    time. In a single-process refresh, snapshots fetched *after* this
    package was first imported would be missed. Calling this at the start
    of a normalize run guarantees every concrete endpoint present on disk
    is registered, regardless of import order. Idempotent — each module's
    registrar skips already-registered keys.
    """
    for mod in (
        bse_issue_detail,
        bse_bid_summary,
        bse_demand_schedule,
        bse_ipo_performance,
        moneycontrol_listed,
        nse_bid_summary,
        nse_demand_data,
        nse_offer_document_details,
    ):
        registrar = getattr(mod, "_register_concrete", None)
        if callable(registrar):
            registrar()


__all__ = [
    "PARSERS",
    "ParserContext",
    "ParserResult",
    "register_parser",
    "parser_for",
    "register_concrete_endpoints",
]
