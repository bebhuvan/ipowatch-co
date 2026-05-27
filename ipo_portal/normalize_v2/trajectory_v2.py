"""Subscription trajectory pipeline for v2.

Builds ``data/site_v2/trajectories/<slug>.json`` — the hourly time-series
of subscription (bid-book) observations for each IPO that was active
during a fetch window.

Rather than re-derive the gnarly NSE/BSE category-parsing logic, this
reuses the battle-tested extractors from the v1 ``ipo_portal.trajectory``
module (``extract_bse_observation`` / ``extract_nse_observation`` and the
category-key mappers). The v2 additions are:

* **Mapping to the canonical v2 slug.** A bid-detail snapshot is keyed by
  BSE ``IPO_NO`` (endpoint ``consolidated_bid_details_new_<IPO_NO>``) or
  NSE symbol (``consolidated_bid_details_<symbol>``). We resolve these to
  the v2 slug via the alias index built from ``issues/by-slug/``
  (``bse:ipo_no:<n>`` aliases and ``identity.symbol``).
* **v2 envelope + provenance** on each trajectory file.
* **Freeze** 7 days after close (carried over from v1) so historical
  trajectories don't churn.

Observations dedupe by ``(source, observed_at)``; merging is monotonic.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..storage import write_json
from ..trajectory import (
    extract_bse_observation,
    extract_nse_observation,
    merge_observations,
)
from ..orchestrator.metadata import SourceRef, build_envelope


DEFAULT_RAW_ROOT = Path("data/raw")
DEFAULT_SITE_V2 = Path("data/site_v2")
FREEZE_GRACE_DAYS = 7

_BSE_IPO_NO_RE = re.compile(r"consolidated_bid_details(?:_new)?_(\d+)$")
_BSE_DEMAND_SCHEDULE_RE = re.compile(r"demand_schedule_(\d+)$")
_NSE_SYMBOL_RE = re.compile(r"consolidated_bid_details_([a-z0-9]+)$", re.IGNORECASE)
_NSE_DEMAND_RE = re.compile(r"demand_data_(nse|all)_([a-z0-9]+)$", re.IGNORECASE)


def build_alias_index(site_v2: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Return (bse_ipo_no → slug, symbol_upper → slug) from v2 records."""
    by_ipo_no: dict[str, str] = {}
    by_symbol: dict[str, str] = {}
    by_slug = site_v2 / "issues" / "by-slug"
    if not by_slug.exists():
        return by_ipo_no, by_symbol
    for path in by_slug.glob("*.json"):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        slug = doc.get("slug")
        if not slug:
            continue
        identity = doc.get("identity") or {}
        symbol = identity.get("symbol")
        if symbol:
            by_symbol[str(symbol).upper()] = slug
        for alias in identity.get("aliases") or []:
            if isinstance(alias, str) and alias.startswith("bse:ipo_no:"):
                by_ipo_no[alias.split(":", 2)[2]] = slug
    return by_ipo_no, by_symbol


def resolve_slug(
    source: str,
    endpoint: str,
    by_ipo_no: dict[str, str],
    by_symbol: dict[str, str],
) -> str | None:
    """Map a bid-detail endpoint name to its v2 slug."""
    if source == "bse":
        m = _BSE_IPO_NO_RE.search(endpoint) or _BSE_DEMAND_SCHEDULE_RE.search(endpoint)
        if m:
            return by_ipo_no.get(m.group(1))
    if source == "nse":
        m = _NSE_SYMBOL_RE.search(endpoint)
        if m:
            return by_symbol.get(m.group(1).upper())
        m = _NSE_DEMAND_RE.search(endpoint)
        if m:
            return by_symbol.get(m.group(2).upper())
    return None


def extract_nse_demand_curve(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """Parse NSE Demand Data rows into a price-level cumulative demand curve."""
    meta = snapshot.get("meta") or {}
    body = snapshot.get("body")
    if not isinstance(body, list):
        return None
    observed_at = meta.get("fetched_at")
    if not observed_at:
        return None
    endpoint = str(meta.get("endpoint") or "")
    scope_match = _NSE_DEMAND_RE.search(endpoint)
    scope = scope_match.group(1).lower() if scope_match else "nse"
    source_updated_at: str | None = None
    points: list[dict[str, Any]] = []
    for row in body:
        if not isinstance(row, dict):
            continue
        price = _paise(row.get("price"))
        cumulative = _int(row.get("cumQty"))
        if price is None or cumulative is None:
            continue
        timestamp = _clean(row.get("timestamp"))
        if timestamp:
            source_updated_at = source_updated_at or timestamp
        points.append({"price_paise": price, "cumulative_quantity": cumulative})
    if not points:
        return None
    points.sort(key=lambda item: item["price_paise"], reverse=True)
    return {
        "observed_at": observed_at,
        "source": "nse",
        "scope": scope,
        "source_updated_at": source_updated_at,
        "points": points,
    }


def extract_bse_demand_schedule_observation(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """Parse BSE demand schedule rows into category-wise bid observations.

    `Pubissues_BSEDemandSchedule_otb_ng` does not publish shares offered or
    subscription multiples. It is still primary demand data, so we preserve the
    observed bid quantities with null offered/times values.
    """
    from ..trajectory import bse_category_key, is_header_row, is_total_row, parse_int

    meta = snapshot.get("meta") or {}
    body = snapshot.get("body")
    rows = body.get("table1") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        return None
    observed_at = meta.get("fetched_at")
    if not observed_at:
        return None
    categories: dict[str, dict[str, Any]] = {}
    total_bid: int | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        srno = row.get("SRNo")
        label = row.get("col2")
        if is_header_row(srno, label):
            continue
        shares_bid = parse_int(row.get("col4"))
        if is_total_row(srno, label):
            total_bid = shares_bid
            continue
        key = bse_category_key(str(srno or ""))
        if key is None:
            continue
        categories[key] = {
            "shares_bid": shares_bid,
            "shares_offered": None,
            "times": None,
        }
    if not categories and total_bid is None:
        return None
    return {
        "observed_at": observed_at,
        "source": "bse",
        "source_updated_at": None,
        "total": {
            "shares_bid": total_bid,
            "shares_offered": None,
            "times": None,
        },
        "categories": categories,
    }


def merge_demand_curves(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for curve in existing:
        by_key[(curve.get("source", ""), curve.get("scope", ""), curve.get("observed_at", ""))] = curve
    for curve in incoming:
        by_key[(curve.get("source", ""), curve.get("scope", ""), curve.get("observed_at", ""))] = curve
    return sorted(by_key.values(), key=lambda item: (item.get("observed_at") or "", item.get("source") or "", item.get("scope") or ""))


def build_trajectories(
    raw_root: Path = DEFAULT_RAW_ROOT,
    site_v2: Path = DEFAULT_SITE_V2,
) -> dict[str, int]:
    """Walk bid-detail snapshots, extract observations, write per-slug files."""
    by_ipo_no, by_symbol = build_alias_index(site_v2)

    # Collect observations and demand curves per slug across all snapshots.
    per_slug: dict[str, list[dict[str, Any]]] = {}
    demand_per_slug: dict[str, list[dict[str, Any]]] = {}
    sources_seen: dict[str, set[tuple[str, str]]] = {}

    for source in ("bse", "nse"):
        src_dir = raw_root / source
        if not src_dir.exists():
            continue
        for endpoint_dir in src_dir.iterdir():
            if not endpoint_dir.is_dir():
                continue
            if (
                "bid_details" not in endpoint_dir.name
                and not endpoint_dir.name.startswith("demand_data_")
                and not endpoint_dir.name.startswith("demand_schedule_")
            ):
                continue
            slug = resolve_slug(source, endpoint_dir.name, by_ipo_no, by_symbol)
            if slug is None:
                continue
            for snap_path in sorted(endpoint_dir.glob("*.json")):
                try:
                    snapshot = json.loads(snap_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
                if source == "nse" and endpoint_dir.name.startswith("demand_data_"):
                    curve = extract_nse_demand_curve(snapshot)
                    if curve is None:
                        continue
                    demand_per_slug.setdefault(slug, []).append(curve)
                    sources_seen.setdefault(slug, set()).add(
                        (source, endpoint_dir.name)
                    )
                    continue
                if source == "bse" and endpoint_dir.name.startswith("demand_schedule_"):
                    obs = extract_bse_demand_schedule_observation(snapshot)
                    if obs is None:
                        continue
                    per_slug.setdefault(slug, []).append(obs)
                    sources_seen.setdefault(slug, set()).add(
                        (source, endpoint_dir.name)
                    )
                    continue
                if source == "bse":
                    obs = extract_bse_observation(snapshot)
                else:
                    obs = extract_nse_observation(snapshot)
                if obs is None:
                    continue
                per_slug.setdefault(slug, []).append(obs)
                sources_seen.setdefault(slug, set()).add(
                    (source, endpoint_dir.name)
                )

    written = 0
    traj_dir = site_v2 / "trajectories"
    for slug in sorted(set(per_slug) | set(demand_per_slug)):
        observations = per_slug.get(slug, [])
        merged = merge_observations([], observations)
        demand_curves = merge_demand_curves([], demand_per_slug.get(slug, []))
        envelope = build_envelope(
            schema_name="trajectory.schema",
            schema_version="2.0.0",
            sources=[
                SourceRef(source=s, endpoint=e, snapshot_at="", confidence="primary")
                for (s, e) in sorted(sources_seen.get(slug, set()))
            ],
            schema_url_self=f"data/site_v2/trajectories/{slug}.json",
            notes="Hourly subscription (bid-book) observations during the active issue window.",
        )
        document = {
            **envelope,
            "issue_slug": slug,
            "frozen_at": _freeze_marker(merged),
            "observation_count": len(merged),
            "observations": merged,
            "demand_curve_count": len(demand_curves),
            "demand_curves": demand_curves,
        }
        if write_json(traj_dir / f"{slug}.json", document):
            written += 1

    # Prune orphan trajectory files — slugs that no longer exist as issues
    # (e.g. after a consolidation merge changed a record's slug). Keeps the
    # trajectories/ dir in lockstep with issues/by-slug/ so the manifest
    # count matches what's on disk.
    pruned = 0
    by_slug_dir = site_v2 / "issues" / "by-slug"
    if traj_dir.exists() and by_slug_dir.exists():
        live = {p.stem for p in by_slug_dir.glob("*.json")}
        for tf in traj_dir.glob("*.json"):
            if tf.stem not in live:
                tf.unlink()
                pruned += 1

    on_disk = len(list(traj_dir.glob("*.json"))) if traj_dir.exists() else 0
    return {"trajectories": on_disk, "written": written, "pruned": pruned}


def _freeze_marker(observations: list[dict[str, Any]]) -> str | None:
    """Stamp a freeze time if the latest observation is older than grace.

    Mirrors v1 behavior: once an issue has been closed past the grace
    window, its trajectory is considered frozen and shouldn't keep
    accreting (E.SUB.004).
    """
    if not observations:
        return None
    latest = max((o.get("observed_at") or "") for o in observations)
    if not latest:
        return None
    try:
        latest_dt = datetime.fromisoformat(latest.replace("Z", "+00:00"))
    except ValueError:
        return None
    from ..orchestrator.metadata import utc_now_iso

    build_dt = datetime.fromisoformat(utc_now_iso().replace("Z", "+00:00"))
    age_days = (build_dt - latest_dt).days
    if age_days >= FREEZE_GRACE_DAYS:
        return (latest_dt + timedelta(days=FREEZE_GRACE_DAYS)).replace(microsecond=0).isoformat()
    return None


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"-", "--", "NA", "N/A", "None", "null"}:
        return None
    return text


def _int(value: Any) -> int | None:
    text = _clean(value)
    if text is None:
        return None
    try:
        return int(float(text.replace(",", "")))
    except ValueError:
        return None


def _paise(value: Any) -> int | None:
    number = _int(value)
    return None if number is None else number * 100
