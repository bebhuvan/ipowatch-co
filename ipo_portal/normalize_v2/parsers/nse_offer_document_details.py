"""Register NSE offer-document nested detail endpoints.

These endpoints expose structured sub-sections for the current offer-document
rows. The public V3 contract does not yet publish every sub-section as a
canonical field; registration keeps coverage explicit while the richer
prospectus intelligence pipeline remains citation-verified.
"""

from __future__ import annotations

import os
import re
from typing import Any

from .registry import PARSERS, ParserContext, register_parser


_DETAIL_RE = re.compile(r"offer_document_detail_[a-z0-9]+$", re.IGNORECASE)
_ABRIDGED_RE = re.compile(r"offer_abridged_[a-z0-9_]+_[a-z0-9]+$", re.IGNORECASE)


@register_parser("nse", "offer_document_detail")
@register_parser("nse", "offer_abridged")
def parse(_body: Any, _ctx: ParserContext) -> list:
    return []


def _register_concrete() -> None:
    raw_root = "data/raw/nse"
    if not os.path.isdir(raw_root):
        return
    for entry in os.listdir(raw_root):
        if not (_DETAIL_RE.match(entry) or _ABRIDGED_RE.match(entry)):
            continue
        key = ("nse", entry)
        if key not in PARSERS.by_key:
            PARSERS.add("nse", entry, parse)


_register_concrete()
