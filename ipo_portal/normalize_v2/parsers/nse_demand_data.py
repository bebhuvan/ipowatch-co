"""Register NSE demand-data endpoints for coverage and trajectory ingestion.

``demand_data_nse_<symbol>`` and ``demand_data_all_<symbol>`` are the
price-level cumulative demand tables behind the NSE "Demand Data" tabs. They
are not merged into issue core records because the full historical stream lives
in ``trajectories/<slug>.json``. This parser intentionally returns no
contributions; registration still makes source coverage explicit and prevents
these primary endpoints being reported as parser gaps.
"""

from __future__ import annotations

import os
import re
from typing import Any

from ..pipeline import Contribution
from .registry import PARSERS, ParserContext, register_parser


_DEMAND_RE = re.compile(r"^demand_data_(?:nse|all)_[a-z0-9]+$", re.IGNORECASE)


def parse(body: Any, ctx: ParserContext) -> list[Contribution]:
    return []


def _register_concrete() -> None:
    raw_root = "data/raw/nse"
    if not os.path.isdir(raw_root):
        return
    for entry in os.listdir(raw_root):
        if not _DEMAND_RE.match(entry):
            continue
        key = ("nse", entry)
        if key not in PARSERS.by_key:
            PARSERS.add("nse", entry, parse)


_register_concrete()

