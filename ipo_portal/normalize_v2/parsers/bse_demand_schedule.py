"""Register BSE demand schedule endpoints for coverage.

The actual time-series extraction is handled by
``normalize_v2.trajectory_v2`` because these snapshots are trajectory data, not
canonical issue core fields. Registering the concrete endpoints here prevents
primary BSE demand schedules from being reported as parser gaps.
"""

from __future__ import annotations

import os
import re
from typing import Any

from .registry import PARSERS, ParserContext, register_parser


_DEMAND_SCHEDULE_RE = re.compile(r"demand_schedule_\d+$")


@register_parser("bse", "demand_schedule")
def parse(_body: Any, _ctx: ParserContext) -> list:
    return []


def _register_concrete() -> None:
    raw_root = "data/raw/bse"
    if not os.path.isdir(raw_root):
        return
    for entry in os.listdir(raw_root):
        if not _DEMAND_SCHEDULE_RE.match(entry):
            continue
        key = ("bse", entry)
        if key not in PARSERS.by_key:
            PARSERS.add("bse", entry, parse)


_register_concrete()
