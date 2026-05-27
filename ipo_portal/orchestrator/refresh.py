"""Daily / periodic refresh orchestration for the v2 pipeline.

One command runs the whole go-forward cycle:

  1. **SEBI scrape**     — newest DRHP filings (earliest IPO signal).
  2. **NSE/BSE fetch**   — the existing v1 fetch (current + document feeds).
  3. **normalize**       — rebuild data/site_v2/ from all raw snapshots.
  4. **enrich-rhp**      — extract prospectus data for current IPOs only.
  5. **export-v3**       — materialize the self-contained website dataset.
  6. **drift**           — detect upstream schema changes.

Each step is independently guarded: a failure in one (e.g., NSE rate-
limiting) is logged and the cycle continues, so a transient network
problem doesn't block the rest. A run summary (per-step status + timing)
is written to ``data/reports/refresh_runs.jsonl`` for the freshness
audit.

Designed to be the cron entrypoint:

    python -m ipo_portal.orchestrator refresh            # full cycle
    python -m ipo_portal.orchestrator refresh-daily      # V3 daily alias
    python -m ipo_portal.orchestrator refresh-subscriptions
    python -m ipo_portal.orchestrator refresh --skip-fetch  # rebuild only
    python -m ipo_portal.orchestrator refresh --hot       # SEBI + current IPOs only (fast, frequent)
"""

from __future__ import annotations

import json
import shutil
import time
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
REFRESH_LOG = PROJECT_ROOT / "data" / "reports" / "refresh_runs.jsonl"


@dataclass
class StepResult:
    name: str
    status: str            # "ok" | "skipped" | "failed"
    elapsed_ms: int
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "elapsed_ms": self.elapsed_ms,
            "detail": self.detail,
            "error": self.error,
        }


def _run_step(name: str, fn: Callable[[], dict[str, Any]], enabled: bool = True) -> StepResult:
    if not enabled:
        print(f"[refresh] {name}: skipped")
        return StepResult(name=name, status="skipped", elapsed_ms=0)
    print(f"[refresh] {name}: running…", flush=True)
    started = time.monotonic()
    try:
        detail = fn() or {}
        elapsed = int((time.monotonic() - started) * 1000)
        print(f"[refresh] {name}: ok ({elapsed} ms) {detail}", flush=True)
        return StepResult(name=name, status="ok", elapsed_ms=elapsed, detail=detail)
    except Exception as exc:  # noqa: BLE001 — one step failing must not abort the cycle
        elapsed = int((time.monotonic() - started) * 1000)
        print(f"[refresh] {name}: FAILED ({elapsed} ms): {exc!r}", flush=True)
        return StepResult(
            name=name,
            status="failed",
            elapsed_ms=elapsed,
            error="".join(traceback.format_exception_only(type(exc), exc)).strip(),
        )


def run_refresh(
    *,
    skip_fetch: bool = False,
    skip_sebi: bool = False,
    skip_kite: bool = False,
    skip_tijori: bool = False,
    skip_enrich: bool = False,
    hot: bool = False,
    enrich_limit: int | None = 25,
    data_root: Path | None = None,
) -> dict[str, Any]:
    """Run the refresh cycle. Returns a summary dict (also logged)."""
    data_root = data_root or (PROJECT_ROOT / "data")
    raw_root = data_root / "raw"
    site_v2 = data_root / "site_v2"
    site_v3 = data_root / "ipo_watch_v3"
    schema_root = PROJECT_ROOT / "docs" / "schema" / "v2"
    v3_schema_root = PROJECT_ROOT / "docs" / "schema" / "v3"
    backup_root = data_root / ".last_good" / "ipo_watch_v3"
    had_previous_good = _snapshot_last_good(site_v3, backup_root)

    results: list[StepResult] = []

    # 1. SEBI scrape — newest DRHP filings.
    def _sebi() -> dict[str, Any]:
        from ..sebi import scrape

        # Hot mode resolves PDFs (we want the DRHP URL); cap rows for speed.
        path = scrape(root=data_root, resolve_pdfs=True, limit=25 if hot else 50)
        body = json.loads(path.read_text(encoding="utf-8")).get("body") or []
        return {"snapshot": str(path), "filings": len(body)}

    results.append(_run_step("sebi_scrape", _sebi, enabled=not skip_sebi))

    # 2. NSE/BSE fetch — the v1 fetch pipeline (network-dependent).
    def _fetch() -> dict[str, Any]:
        from ..cli import run_fetch

        # Reuse the existing fetch command; "all" covers NSE + BSE.
        rc = run_fetch(data_root, "all", date.today(), allow_validation_errors=True)
        if rc != 0:
            raise RuntimeError(f"NSE/BSE fetch completed with source failures (rc={rc}); previous raw snapshots preserved")
        return {"v1_fetch_rc": rc}

    results.append(_run_step("nse_bse_fetch", _fetch, enabled=not skip_fetch and not hot))

    # 2b. Kite prices — current LTPs + new listing candles → v2 snapshot.
    #     Runs before normalize so the snapshot is in data/raw/kite/.
    #     Skips gracefully if there's no valid Kite session. CI can disable
    #     this while Yahoo is the launch market-data source.
    def _kite() -> dict[str, Any]:
        from ..kite import (
            DEFAULT_DB_PATH,
            DEFAULT_SESSION_PATH,
            backfill_listings,
            init_db,
            refresh_current_prices,
        )
        from ..kite_auth import KiteAuthError, ensure_session
        from ..kite_v2 import export_snapshot

        try:
            ensure_session(DEFAULT_SESSION_PATH)  # TOTP if configured, else existing token
        except KiteAuthError as exc:
            return {"skipped": True, "reason": str(exc).split(".", 1)[0]}
        init_db(DEFAULT_DB_PATH)
        current = refresh_current_prices(DEFAULT_DB_PATH, DEFAULT_SESSION_PATH)
        # Only new listings need candles; backfill is incremental.
        listings = backfill_listings(DEFAULT_DB_PATH, DEFAULT_SESSION_PATH, limit=50)
        snap = export_snapshot(db_path=DEFAULT_DB_PATH, data_root=data_root)
        return {
            "current_ok": current.get("ok"),
            "listing_ok": listings.get("ok"),
            "snapshot": str(snap),
        }

    results.append(_run_step("kite_prices", _kite, enabled=not skip_fetch and not skip_kite))

    # 2c. Yahoo Finance fallback — public current/listing prices → v2 snapshot.
    #     This keeps market-performance calculations populated while Kite
    #     credentials are unavailable. Kite still wins precedence when present.
    def _yahoo() -> dict[str, Any]:
        from ..yahoo_v2 import export_snapshot

        if not (site_v2 / "issues" / "by-slug").exists():
            return {"skipped": True, "reason": "missing site_v2; run normalize once before Yahoo fallback"}
        snap = export_snapshot(site_root=site_v2, data_root=data_root)
        body = json.loads(snap.read_text(encoding="utf-8")).get("body") or []
        ok = sum(1 for row in body if isinstance(row, dict) and row.get("status") == "ok")
        no_prices = sum(1 for row in body if isinstance(row, dict) and row.get("status") == "no_prices")
        errors = sum(1 for row in body if isinstance(row, dict) and row.get("status") == "error")
        if errors and not ok:
            raise RuntimeError(f"Yahoo fallback produced no usable market rows and {errors} errors")
        return {
            "snapshot": str(snap),
            "rows": len(body),
            "ok": ok,
            "no_prices": no_prices,
            "errors": errors,
        }

    results.append(_run_step("yahoo_prices", _yahoo, enabled=not skip_fetch))

    # 2d. Tijori Kite screener feed — public company/sector/peer enrichment.
    #     Normalization reads data/derived/sector_map.json, so this must run
    #     before normalize when fresh Tijori sector data is desired.
    def _tijori() -> dict[str, Any]:
        from ..tijori import (
            TIJORI_IPO_URL,
            fetch_tijori_ipo_feed,
            write_sector_map_from_tijori,
            write_tijori_enrichment,
        )
        from ..storage import append_source_event, save_raw_snapshot

        try:
            rows = fetch_tijori_ipo_feed()
        except Exception as exc:  # noqa: BLE001 - Tijori is enrichment, not a primary-source gate.
            append_source_event(
                data_root,
                {
                    "source": "tijori",
                    "endpoint": "ipo_feed",
                    "url": TIJORI_IPO_URL,
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "stale_if_fail": True,
                },
            )
            return {"skipped": True, "reason": f"{type(exc).__name__}: {exc}"}

        snapshot = save_raw_snapshot(
            data_root,
            "tijori",
            "ipo_feed",
            TIJORI_IPO_URL,
            rows,
            status_code=200,
        )
        enrichment = write_tijori_enrichment(rows, data_root / "derived" / "tijori_ipo_enrichment.json")
        sector_map = write_sector_map_from_tijori(enrichment, data_root / "derived" / "sector_map.json")
        stats = enrichment.get("stats") or {}
        return {
            "snapshot": str(snapshot),
            "rows": stats.get("rows"),
            "with_isin": stats.get("with_isin"),
            "with_financials": stats.get("with_financials"),
            "sector_map_entries": len(sector_map),
        }

    results.append(_run_step("tijori_ipo_feed", _tijori, enabled=not skip_tijori))

    # 3. normalize — rebuild data/site_v2/.
    def _normalize() -> dict[str, Any]:
        from ..normalize_v2.pipeline import run_normalize

        run_normalize(raw_root=raw_root, out_root=site_v2, schema_root=schema_root)
        manifest = json.loads((site_v2 / "manifest.json").read_text(encoding="utf-8"))
        return {
            "issues_published": manifest.get("issues_published"),
            "quarantined": manifest.get("issues_quarantined"),
            "companies": manifest.get("companies_total"),
        }

    results.append(_run_step("normalize", _normalize))

    # 4. enrich-rhp — current IPOs only (no backfill).
    def _enrich() -> dict[str, Any]:
        from .cli import _load_rhp_extractor, _run_enrich_scan
        import argparse

        module = _load_rhp_extractor()
        ns = argparse.Namespace(
            scan_pending=True,
            recent_days=45,
            limit=enrich_limit,
            site_root=site_v2,
            url=None,
            slug=None,
        )
        rc = _run_enrich_scan(ns, module)
        return {"enrich_rc": rc}

    results.append(_run_step("enrich_rhp", _enrich, enabled=not skip_enrich))

    # 5. export-v3 — website-facing self-contained tree.
    def _export_v3() -> dict[str, Any]:
        from ..site_v3 import export_v3

        stats = export_v3(source_root=site_v2, out_root=site_v3, schema_root=v3_schema_root)
        return {
            "issues": stats.issues,
            "companies": stats.companies,
            "trajectories": stats.trajectories,
            "prospectuses": stats.prospectuses,
            "dataset_version": stats.dataset_version,
        }

    results.append(_run_step("export_v3", _export_v3))

    # 6. drift detection.
    def _drift() -> dict[str, Any]:
        from .drift import scan_drift

        report = scan_drift(
            raw_root=raw_root,
            catalog_root=PROJECT_ROOT / "docs" / "schema" / "raw_catalog",
            drift_log=PROJECT_ROOT / "data" / "reports" / "upstream_drift.jsonl",
        )
        detail = {
            "inspected": report.inspected,
            "events": len(report.events),
            "blocking": len(report.blocking),
        }
        return detail

    results.append(_run_step("drift", _drift, enabled=not hot))

    restored_last_good = False
    if any(r.status == "failed" for r in results) and had_previous_good:
        _restore_last_good(backup_root, site_v3)
        restored_last_good = True

    summary = {
        "started_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "mode": "hot" if hot else "full",
        "steps": [r.to_dict() for r in results],
        "ok": all(r.status != "failed" for r in results),
        "last_good_build": {
            "backup_path": str(backup_root),
            "available": had_previous_good,
            "restored": restored_last_good,
        },
    }
    _append_log(summary)
    return summary


def run_subscription_refresh(
    *,
    skip_fetch: bool = False,
    data_root: Path | None = None,
) -> dict[str, Any]:
    """Refresh only active-issue subscription artifacts in V3.

    The command may still update raw snapshots and the normalized staging tree
    so parsers have fresh exchange data, but the public V3 write set is limited
    to active issue subscription JSON, matching summaries, and trajectories.
    """
    data_root = data_root or (PROJECT_ROOT / "data")
    raw_root = data_root / "raw"
    site_v2 = data_root / "site_v2"
    site_v3 = data_root / "ipo_watch_v3"
    schema_root = PROJECT_ROOT / "docs" / "schema" / "v2"

    results: list[StepResult] = []

    def _fetch() -> dict[str, Any]:
        from ..cli import run_fetch

        rc = run_fetch(data_root, "all", date.today(), allow_validation_errors=True)
        if rc != 0:
            raise RuntimeError(f"NSE/BSE fetch completed with source failures (rc={rc}); subscription public files were not updated")
        return {"v1_fetch_rc": rc}

    results.append(_run_step("nse_bse_fetch", _fetch, enabled=not skip_fetch))

    def _normalize() -> dict[str, Any]:
        from ..normalize_v2.pipeline import run_normalize

        run_normalize(raw_root=raw_root, out_root=site_v2, schema_root=schema_root)
        manifest = json.loads((site_v2 / "manifest.json").read_text(encoding="utf-8"))
        return {
            "issues_published": manifest.get("issues_published"),
            "companies": manifest.get("companies_total"),
        }

    results.append(_run_step("normalize", _normalize))

    def _subscription_delta() -> dict[str, Any]:
        from ..site_v3.export import update_v3_subscriptions

        stats = update_v3_subscriptions(source_root=site_v2, out_root=site_v3)
        return stats.to_dict()

    results.append(_run_step("v3_subscription_delta", _subscription_delta))

    summary = {
        "started_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "mode": "subscriptions",
        "steps": [r.to_dict() for r in results],
        "ok": all(r.status != "failed" for r in results),
    }
    _append_log(summary)
    return summary


def _append_log(summary: dict[str, Any]) -> None:
    REFRESH_LOG.parent.mkdir(parents=True, exist_ok=True)
    with REFRESH_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    latest = REFRESH_LOG.parent / "latest_refresh_summary.json"
    latest.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _snapshot_last_good(site_v3: Path, backup_root: Path) -> bool:
    manifest_path = site_v3 / "manifest.json"
    if not manifest_path.exists():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    if manifest.get("degraded") is True:
        return backup_root.exists()
    if backup_root.exists():
        shutil.rmtree(backup_root)
    backup_root.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(site_v3, backup_root)
    return True


def _restore_last_good(backup_root: Path, site_v3: Path) -> None:
    if not backup_root.exists():
        return
    tmp = site_v3.with_name(f".{site_v3.name}.restore")
    if tmp.exists():
        shutil.rmtree(tmp)
    shutil.copytree(backup_root, tmp)
    if site_v3.exists():
        shutil.rmtree(site_v3)
    tmp.replace(site_v3)
