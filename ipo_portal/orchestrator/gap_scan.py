"""Phase 5: surface first-class fields the catalog flags but v1 records lack.

We walk the Phase 1 catalog (every endpoint's contamination_risks +
canonical field hints) and cross-reference against the current
``data/site/issues/by-slug/`` records. Fields the catalog identifies
as available upstream but absent from the canonical record are
listed as gaps — the v2 normalizer must populate them.

Output: ``data/reports/gap_scan.json``.

This is deterministic (no DeepSeek call needed) — Phase 1 already
did the analysis. Gap-scan is just a join + report.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..storage import write_json
from . import PIPELINE_NAME, __version__
from .metadata import build_envelope, utc_now_iso


DEFAULT_CATALOG_ROOT = Path("docs/schema/raw_catalog")
DEFAULT_SITE_ROOT = Path("data/site")
DEFAULT_REPORT_PATH = Path("data/reports/gap_scan.json")


@dataclass(frozen=True)
class FieldGap:
    canonical_hint: str
    sources: list[tuple[str, str]]  # (source, endpoint) tuples
    sample_examples: list[Any] = field(default_factory=list)
    found_in_v1_records: int = 0
    contamination_risks: list[dict[str, Any]] = field(default_factory=list)


def collect_catalog_hints(catalog_root: Path) -> dict[str, list[dict[str, Any]]]:
    """Return ``{canonical_hint: [hint_entry, ...]}`` from all catalogs."""
    hints: dict[str, list[dict[str, Any]]] = {}
    for catalog_file in sorted(catalog_root.glob("*/*.json")):
        try:
            doc = json.loads(catalog_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        source = doc.get("source")
        endpoint = doc.get("endpoint")
        for f in doc.get("fields") or []:
            hint = f.get("canonical_field_hint")
            if not hint:
                continue
            hints.setdefault(hint, []).append(
                {
                    "source": source,
                    "endpoint": endpoint,
                    "upstream_path": f.get("path"),
                    "upstream_name": f.get("name"),
                    "type": f.get("type"),
                    "units": f.get("units"),
                    "examples": (f.get("examples") or [])[:3],
                    "contamination_risks": f.get("contamination_risks") or [],
                }
            )
    return hints


def collect_v1_field_paths(site_root: Path, limit: int | None = None) -> Counter[str]:
    """Walk v1 by-slug records and return a Counter of dotted paths.

    Used to count how many v1 records already populate a given path so
    gap-scan can show field coverage (e.g., "0/11662 records have
    `subscription.anchor.allotment_paise`").
    """
    counter: Counter[str] = Counter()
    by_slug = site_root / "issues" / "by-slug"
    if not by_slug.exists():
        return counter
    n = 0
    for path in by_slug.glob("*.json"):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        _walk_paths(doc, "", counter)
        n += 1
        if limit and n >= limit:
            break
    return counter


def _walk_paths(value: Any, prefix: str, counter: Counter[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            new_prefix = f"{prefix}.{key}" if prefix else key
            _walk_paths(child, new_prefix, counter)
        return
    if isinstance(value, list):
        for child in value:
            _walk_paths(child, prefix, counter)
        return
    if value is not None and value != "":
        counter[prefix] += 1


def hint_matches_path(hint: str, path: str) -> bool:
    """Loose match: the canonical hint matches a v1 path.

    We don't require exact identity — v1 paths like
    ``pricing.price_band_high`` should match canonical hint
    ``pricing.price_band.upper_paise`` if the prefix segment overlaps.
    """
    return _normalize_path(hint) in _normalize_path(path) or _normalize_path(path) in _normalize_path(hint)


def _normalize_path(text: str) -> str:
    return text.replace("_paise", "").replace("_inr_text", "").replace("_x", "").replace("_bps", "").replace(".", "/").lower()


def scan_gaps(
    catalog_root: Path = DEFAULT_CATALOG_ROOT,
    site_root: Path = DEFAULT_SITE_ROOT,
    report_path: Path = DEFAULT_REPORT_PATH,
    v1_sample_limit: int | None = 500,
) -> Path:
    """Produce a gap-scan report; returns report_path."""
    hints = collect_catalog_hints(catalog_root)
    v1_paths = collect_v1_field_paths(site_root, limit=v1_sample_limit)

    gaps: list[dict[str, Any]] = []
    covered: list[dict[str, Any]] = []
    for hint, entries in sorted(hints.items()):
        sample_examples = []
        risks: list[dict[str, Any]] = []
        for e in entries:
            sample_examples.extend(e.get("examples") or [])
            risks.extend(e.get("contamination_risks") or [])
        sources = sorted({(e["source"], e["endpoint"]) for e in entries})

        # Count how many v1 paths plausibly contain this canonical hint.
        match_count = sum(count for path, count in v1_paths.items() if hint_matches_path(hint, path))

        record = {
            "canonical_hint": hint,
            "sources": [list(s) for s in sources],
            "sample_examples": sample_examples[:5],
            "contamination_risks": risks,
            "v1_coverage_observed": match_count,
        }
        if match_count == 0:
            gaps.append(record)
        else:
            covered.append(record)

    envelope = build_envelope(
        schema_name="gap_scan.schema",
        schema_version="1.0.0",
        notes=(
            "Gap-scan: canonical fields the Phase 1 catalog identifies as "
            "available upstream but not populated (or under-populated) in "
            "the v1 site output. The v2 normalizer must close these gaps."
        ),
    )
    document = {
        **envelope,
        "schema_url_self": "data/reports/gap_scan.json",
        "generated_at": utc_now_iso(),
        "generated_by": f"{PIPELINE_NAME}/{__version__}",
        "v1_sample_limit": v1_sample_limit,
        "gaps_count": len(gaps),
        "covered_count": len(covered),
        "gaps": gaps,
        "covered": covered,
    }
    write_json(report_path, document)
    return report_path
