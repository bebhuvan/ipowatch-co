"""Pick representative raw snapshots per source/endpoint for analysis.

Phase 1 (catalog) needs a single high-information snapshot per endpoint to
send to DeepSeek. "High-information" means: largest body, taken from the
latest successful fetch, with a non-empty payload. We deduplicate the
suffix-keyed dynamic endpoints (e.g., ``consolidated_bid_details_7722``,
``consolidated_bid_details_7727``) down to one canonical group key (e.g.,
``consolidated_bid_details_<n>``) so we don't ask DeepSeek to catalog the
same shape 50 times.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


# Endpoint name families. Concrete endpoint names whose only difference is a
# trailing ID, year, or page number collapse to one family key — we send
# DeepSeek one representative snapshot per family rather than per concrete
# endpoint. (No point cataloguing ten years of identically-shaped BSE
# performance dumps.)
#
# Two kinds of patterns:
# 1. PREFIX_FAMILIES — collapse "<prefix><id>" -> "<prefix><id>".
# 2. REGEX_FAMILIES  — collapse via regex substitution to a placeholder name.
PREFIX_FAMILIES: dict[str, list[str]] = {
    "nse": [
        "issue_detail_",
        "bid_details_",
        "consolidated_bid_details_",
        "demand_data_nse_",
        "demand_data_all_",
        "offer_document_detail_",
        "offer_abridged_",
    ],
    "bse": [
        "issue_detail_",
        "bid_details_",
        "consolidated_bid_details_",
        "consolidated_bid_details_new_",
        "demand_schedule_",
        "demand_graph_bse_",
        "demand_graph_consolidated_",
    ],
}

# Regex-based family collapse (apply to endpoint name within a source).
REGEX_FAMILIES: dict[str, list[tuple[re.Pattern[str], str]]] = {
    "bse": [
        (re.compile(r"^ipo_performance_mainboard_\d{4}$"), "ipo_performance_mainboard_<year>"),
        (re.compile(r"^ipo_performance_sme_\d{4}$"), "ipo_performance_sme_<year>"),
    ],
    "capitalmarket": [
        (re.compile(r"^ipo_historic_table_page_\d+$"), "ipo_historic_table_page_<n>"),
        (re.compile(r"^sme_historic_table_page_\d+$"), "sme_historic_table_page_<n>"),
    ],
    "moneycontrol": [
        (re.compile(r"^listed_ipos_page_\d+$"), "listed_ipos_page_<n>"),
    ],
    "trendlyne": [
        (re.compile(r"^year_\d{4}$"), "year_<year>"),
    ],
}


@dataclass(frozen=True)
class EndpointSample:
    source: str
    endpoint_group: str
    endpoint_concrete: str
    snapshot_path: Path
    snapshot_at: str
    url: str
    body_bytes: int


def family_key(source: str, endpoint: str) -> str:
    """Collapse suffix-bearing endpoints to a family key.

    Examples
    --------
    >>> family_key("bse", "consolidated_bid_details_new_7722")
    'consolidated_bid_details_new_<id>'
    >>> family_key("bse", "ipo_performance_sme_2024")
    'ipo_performance_sme_<year>'
    >>> family_key("moneycontrol", "listed_ipos_page_000020")
    'listed_ipos_page_<n>'
    >>> family_key("nse", "ipo_current_issue")
    'ipo_current_issue'
    """
    for pattern, replacement in REGEX_FAMILIES.get(source, []):
        if pattern.match(endpoint):
            return replacement
    for prefix in PREFIX_FAMILIES.get(source, []):
        if endpoint.startswith(prefix):
            return f"{prefix}<id>"
    return endpoint


def iter_endpoint_samples(raw_root: Path) -> Iterator[EndpointSample]:
    """Yield one EndpointSample per (source, endpoint_group).

    Picks the largest body across all concrete endpoints in the group, from
    its latest snapshot. Skips groups whose every snapshot is empty.
    """
    if not raw_root.exists():
        return

    by_group: dict[tuple[str, str], EndpointSample] = {}
    for source_dir in sorted(raw_root.iterdir()):
        if not source_dir.is_dir():
            continue
        source = source_dir.name
        for endpoint_dir in sorted(source_dir.iterdir()):
            if not endpoint_dir.is_dir():
                continue
            endpoint = endpoint_dir.name
            snapshots = sorted(endpoint_dir.glob("*.json"))
            if not snapshots:
                continue
            latest = snapshots[-1]
            try:
                payload = json.loads(latest.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            meta = payload.get("meta", {}) or {}
            body = payload.get("body")
            body_bytes = len(json.dumps(body, ensure_ascii=False)) if body is not None else 0
            if body_bytes == 0:
                continue

            group = family_key(source, endpoint)
            sample = EndpointSample(
                source=source,
                endpoint_group=group,
                endpoint_concrete=endpoint,
                snapshot_path=latest,
                snapshot_at=str(meta.get("fetched_at") or ""),
                url=str(meta.get("url") or ""),
                body_bytes=body_bytes,
            )
            existing = by_group.get((source, group))
            if existing is None or sample.body_bytes > existing.body_bytes:
                by_group[(source, group)] = sample

    for sample in sorted(by_group.values(), key=lambda s: (s.source, s.endpoint_group)):
        yield sample
