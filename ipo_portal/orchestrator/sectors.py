"""Bulk sector/industry classification for v2 issues (DeepSeek).

Sector is in no NSE/BSE feed. For issues we've RHP-extracted it comes
from the prospectus (``company_about.sector``); for the rest we classify
the company name + any business hint with DeepSeek — exactly the bulk
data-enrichment role DeepSeek plays in this repo.

Flow:
1. Walk ``data/site_v2/issues/by-slug/`` for issues with no sector yet
   (and no prospectus sector already applied).
2. Batch their ``(slug, company_name)`` (~60/call) to DeepSeek for a
   canonical sector + industry.
3. Write the merged map to ``data/derived/sector_map.json``
   (``{slug: {sector, industry, source}}``).

The map is an INPUT to ``normalize`` — the pipeline applies it onto each
record's ``classification`` block during the build (deterministic, by
slug). So the loop is: normalize → classify-sectors → normalize (applies
the map; cached thereafter).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..deepseek import DeepSeekClient


DEFAULT_SITE_V2 = Path("data/site_v2")
DEFAULT_MAP_PATH = Path("data/derived/sector_map.json")
BATCH = 60

# A small, fixed sector vocabulary keeps the data consistent and joinable.
SECTORS = [
    "Financials", "Healthcare & Pharma", "Information Technology",
    "Industrials & Capital Goods", "Consumer Discretionary",
    "Consumer Staples (FMCG)", "Materials & Chemicals", "Energy & Utilities",
    "Real Estate & Construction", "Communication & Media",
    "Automobile & Components", "Metals & Mining", "Textiles & Apparel",
    "Agriculture & Food Processing", "Logistics & Transportation",
    "Hospitality & Travel", "Infrastructure", "Other",
]

SYSTEM = (
    "You classify Indian listed/issuing companies into a fixed sector "
    "vocabulary from the company name (and any hint). Output STRICT JSON "
    "only. Use exactly one sector from the provided list; if genuinely "
    "unclear, use \"Other\". Give a concise industry within the sector."
)

USER_TMPL = """Classify each company into one sector from this list (use the exact string):
{sectors}

Return JSON: {{ "results": [ {{ "key": "<the key given>", "sector": "<one of the list>", "industry": "<short>" }} ] }}

COMPANIES (key<TAB>name):
{companies}
"""


def _existing_map(path: Path) -> dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("map", {})
        except json.JSONDecodeError:
            return {}
    return {}


def _norm(name: str) -> str:
    from ..normalize_v2.identity import normalize_name

    return normalize_name(name)


def pending_companies(site_v2: Path, existing: dict[str, Any]) -> dict[str, str]:
    """Return ``{normalized_name: representative_display_name}`` for companies
    that don't yet have a sector. Sector is a company-level attribute, so we
    key by normalized name (stable across issue merges / slug changes) and
    classify each company once regardless of how many issues it has."""
    out: dict[str, str] = {}
    by_slug = site_v2 / "issues" / "by-slug"
    for p in sorted(by_slug.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        name = (d.get("identity") or {}).get("company_name")
        if not name:
            continue
        key = _norm(name)
        if not key or key in existing or key in out:
            continue
        if (d.get("classification") or {}).get("sector"):
            continue
        out[key] = name
    return out


def classify(
    site_v2: Path = DEFAULT_SITE_V2,
    map_path: Path = DEFAULT_MAP_PATH,
    limit: int | None = None,
    model: str = "deepseek-chat",
    client: DeepSeekClient | None = None,
) -> dict[str, Any]:
    client = client or DeepSeekClient()
    existing = _existing_map(map_path)
    pending = list(pending_companies(site_v2, existing).items())  # (norm_key, display_name)
    if limit is not None:
        pending = pending[:limit]

    new_count = 0
    for i in range(0, len(pending), BATCH):
        batch = pending[i : i + BATCH]
        companies = "\n".join(f"{key}\t{name}" for key, name in batch)
        resp = client.chat(
            user=USER_TMPL.format(sectors="\n".join(f"- {s}" for s in SECTORS), companies=companies),
            system=SYSTEM,
            response_format="json_object",
            purpose=f"sector_classify:batch{i // BATCH}",
            extra_telemetry={"batch_size": len(batch)},
        )
        body = resp.json_content if isinstance(resp.json_content, dict) else {}
        for r in body.get("results", []) or []:
            key = r.get("key")
            sector = r.get("sector")
            if key and sector:
                existing[key] = {
                    "sector": sector if sector in SECTORS else "Other",
                    "industry": r.get("industry"),
                    "source": "deepseek-classification",
                }
                new_count += 1

    map_path.parent.mkdir(parents=True, exist_ok=True)
    map_path.write_text(
        json.dumps(
            {"generated_by": "ipo_portal.orchestrator.sectors", "keyed_by": "normalized_company_name",
             "count": len(existing), "map": existing},
            ensure_ascii=False, indent=2, sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    return {"classified_new": new_count, "total_in_map": len(existing), "pending_seen": len(pending)}


def load_sector_map(map_path: Path = DEFAULT_MAP_PATH) -> dict[str, Any]:
    return _existing_map(map_path)
