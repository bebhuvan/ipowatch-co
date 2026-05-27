"""Fail-closed publish guard for the IPOWatch V3 artifact tree."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SITE_ROOT = ROOT / "data" / "ipo_watch_v3"
DEFAULT_REFRESH_SUMMARY = ROOT / "data" / "reports" / "latest_refresh_summary.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Guard V3 Git/Cloudflare publication.")
    parser.add_argument("--site-root", type=Path, default=DEFAULT_SITE_ROOT)
    parser.add_argument("--refresh-summary", type=Path, default=DEFAULT_REFRESH_SUMMARY)
    parser.add_argument("--allow-degraded", action="store_true", help="Allow degraded manifests to publish.")
    parser.add_argument("--skip-refresh-summary", action="store_true", help="Do not require latest_refresh_summary.json.")
    args = parser.parse_args()

    findings: list[str] = []
    manifest = _read_json(args.site_root / "manifest.json", findings, "manifest")
    if not isinstance(manifest, dict):
        _print(findings)
        return 1

    _check_manifest(args.site_root, manifest, args.allow_degraded, findings)
    if not args.skip_refresh_summary:
        summary = _read_json(args.refresh_summary, findings, "refresh summary")
        if isinstance(summary, dict):
            _check_refresh_summary(summary, findings)

    _print(findings)
    return 1 if findings else 0


def _check_manifest(site_root: Path, manifest: dict[str, Any], allow_degraded: bool, findings: list[str]) -> None:
    if manifest.get("dataset") != "ipo-watch.site_v3.manifest":
        findings.append("manifest.dataset is not ipo-watch.site_v3.manifest")
    if (manifest.get("site_contract") or {}).get("self_contained") is not True:
        findings.append("manifest.site_contract.self_contained is not true")
    if (manifest.get("site_contract") or {}).get("root") != "data/ipo_watch_v3":
        findings.append("manifest.site_contract.root is not data/ipo_watch_v3")
    if manifest.get("degraded") is True and not allow_degraded:
        stale = manifest.get("stale_sources") or []
        findings.append(f"manifest is degraded; stale_sources={len(stale)}")

    by_slug = site_root / "issues" / "by-slug"
    companies = site_root / "companies" / "by-slug"
    trajectories = site_root / "trajectories"
    counts = {
        "issues_published": len(list(by_slug.glob("*.json"))) if by_slug.exists() else -1,
        "issues_total": len(list(by_slug.glob("*.json"))) if by_slug.exists() else -1,
        "companies_total": len(list(companies.glob("*.json"))) if companies.exists() else -1,
        "trajectories_total": len(list(trajectories.glob("*.json"))) if trajectories.exists() else 0,
    }
    for key, disk_count in counts.items():
        if manifest.get(key) != disk_count:
            findings.append(f"{key} mismatch: manifest={manifest.get(key)} disk={disk_count}")

    index_path = site_root / "issues" / "index.json"
    if not index_path.exists():
        findings.append(f"missing public index: {index_path}")
        return
    index = _read_json(index_path, findings, "public index")
    if isinstance(index, dict):
        public_slugs = {row.get("slug") for row in index.get("items") or [] if isinstance(row, dict)}
        quarantined = {p.stem for p in (site_root / "issues" / "quarantine").glob("*.json")}
        leaked = sorted(public_slugs & quarantined)
        if leaked:
            findings.append(f"quarantined records leaked into public index: {leaked[:10]}")


def _check_refresh_summary(summary: dict[str, Any], findings: list[str]) -> None:
    if summary.get("ok") is not True:
        findings.append("latest refresh summary is not ok")
    failed = [step.get("name") for step in summary.get("steps") or [] if step.get("status") == "failed"]
    if failed:
        findings.append(f"latest refresh has failed steps: {failed}")
    if (summary.get("last_good_build") or {}).get("restored") is True:
        findings.append("latest refresh restored last-good build; publish diagnostics, not data")


def _read_json(path: Path, findings: list[str], label: str) -> Any:
    if not path.exists():
        findings.append(f"missing {label}: {path}")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        findings.append(f"unparseable {label}: {path}: {exc}")
        return None


def _print(findings: list[str]) -> None:
    if not findings:
        print("[guard-publish-v3] publish guard passed")
        return
    print("[guard-publish-v3] publish guard failed")
    for finding in findings:
        print(f"  - {finding}")


if __name__ == "__main__":
    raise SystemExit(main())
