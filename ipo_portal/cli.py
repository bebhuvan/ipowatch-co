from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from .capitalmarket import fetch_capitalmarket_history
from .http import HttpClient
from .moneycontrol import fetch_moneycontrol_listed_ipos
from .normalize import merge_for_site, normalize_snapshots
from .prime import fetch_prime_demo_pages
from .site_builder import build_astro_site_data
from .sources import nested_endpoints_from_snapshots, selected_endpoints
from .storage import append_source_event, load_latest_snapshots, save_raw_snapshot, write_json
from .trendlyne import fetch_trendlyne_ipo_data
from .validate import validate_records


FETCH_SOURCE_CHOICES = ["all", "nse", "bse", "capitalmarket", "prime", "trendlyne", "moneycontrol"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch, normalize, and validate IPO data from NSE/BSE/CapitalMarket/PRIME/Trendlyne/Moneycontrol.")
    add_common_options(parser, default=True)

    subcommands = parser.add_subparsers(dest="command")

    fetch = subcommands.add_parser("fetch", help="Fetch source data and build all outputs.")
    add_common_options(fetch)
    fetch.add_argument("--source", choices=FETCH_SOURCE_CHOICES, default="all")

    build_site = subcommands.add_parser("build-site", help="Build normalized and site JSON from latest raw snapshots.")
    add_common_options(build_site)
    build_site.add_argument("--source", choices=FETCH_SOURCE_CHOICES, default="all")

    validate = subcommands.add_parser("validate", help="Validate data/processed/ipos.json.")
    add_common_options(validate)

    return parser.parse_args()


def add_common_options(parser: argparse.ArgumentParser, default: bool = False) -> None:
    parser.add_argument("--data-dir", default="data" if default else argparse.SUPPRESS, help="Output data directory.")
    parser.add_argument("--as-of", default=date.today().isoformat() if default else argparse.SUPPRESS, help="Point-in-time validation date, YYYY-MM-DD.")
    parser.add_argument(
        "--allow-validation-errors",
        action="store_true",
        default=False if default else argparse.SUPPRESS,
        help="Write outputs but exit 0 even when hard validation errors are quarantined.",
    )


def coerce_as_of(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit(f"--as-of must be YYYY-MM-DD, got {value!r}") from exc


def main() -> int:
    args = parse_args()
    command = args.command or "fetch"
    data_dir = Path(args.data_dir)
    as_of = coerce_as_of(args.as_of)
    allow_validation_errors = bool(getattr(args, "allow_validation_errors", False))

    if command == "fetch":
        return run_fetch(data_dir, args.source, as_of, allow_validation_errors)
    if command == "build-site":
        return run_build_site(data_dir, as_of, allow_validation_errors)
    if command == "validate":
        return run_validate(data_dir, as_of, allow_validation_errors)
    raise SystemExit(f"Unsupported command: {command}")


def run_fetch(data_dir: Path, source: str, as_of: date, allow_validation_errors: bool = False) -> int:
    client = HttpClient()
    snapshots = []
    fetch_failures: list[dict[str, str]] = []
    for endpoint, result in _fetch_with_stale_if_fail(client, selected_endpoints(source, as_of), data_dir, fetch_failures):
        path = save_raw_snapshot(
            data_dir,
            endpoint.source,
            endpoint.name,
            result.url,
            result.body,
            status_code=result.status_code,
            elapsed_ms=result.elapsed_ms,
        )
        snapshots.append(json.loads(path.read_text(encoding="utf-8")))
        print(f"saved {endpoint.source}/{endpoint.name} -> {path}")
    if source in {"all", "capitalmarket"}:
        capitalmarket_snapshots = fetch_capitalmarket_history(client, data_dir)
        snapshots.extend(capitalmarket_snapshots)
        print(f"saved {len(capitalmarket_snapshots)} CapitalMarket snapshots")
    if source in {"all", "prime"}:
        prime_snapshots = fetch_prime_demo_pages(client, data_dir)
        snapshots.extend(prime_snapshots)
        print(f"saved {len(prime_snapshots)} PRIME snapshots")
    if source in {"all", "trendlyne"}:
        trendlyne_snapshots = fetch_trendlyne_ipo_data(client, data_dir, as_of)
        snapshots.extend(trendlyne_snapshots)
        print(f"saved {len(trendlyne_snapshots)} Trendlyne snapshots")
    if source in {"all", "moneycontrol"}:
        moneycontrol_snapshots = fetch_moneycontrol_listed_ipos(client, data_dir)
        snapshots.extend(moneycontrol_snapshots)
        print(f"saved {len(moneycontrol_snapshots)} Moneycontrol snapshots")
    nested_endpoints = nested_endpoints_from_snapshots(snapshots, as_of)
    if nested_endpoints:
        print(f"fetching {len(nested_endpoints)} nested IPO resources")
    for endpoint, result in _fetch_with_stale_if_fail(client, nested_endpoints, data_dir, fetch_failures):
        path = save_raw_snapshot(
            data_dir,
            endpoint.source,
            endpoint.name,
            result.url,
            result.body,
            status_code=result.status_code,
            elapsed_ms=result.elapsed_ms,
        )
        snapshots.append(json.loads(path.read_text(encoding="utf-8")))
        print(f"saved {endpoint.source}/{endpoint.name} -> {path}")
    rc = write_outputs(data_dir, snapshots, as_of, allow_validation_errors)
    return 1 if fetch_failures else rc


def _fetch_with_stale_if_fail(client: HttpClient, endpoints, data_dir: Path, failures: list[dict[str, str]]):
    warmed_nse = False
    for endpoint in endpoints:
        try:
            if endpoint.source == "nse" and not warmed_nse:
                client.warm_nse()
                warmed_nse = True
            result = client.get(endpoint.url, referer=endpoint.referer, expect_json=endpoint.expect_json)
            yield endpoint, result
        except Exception as exc:  # noqa: BLE001 - stale raw snapshot remains the fallback.
            event = {
                "source": endpoint.source,
                "endpoint": endpoint.name,
                "url": endpoint.url,
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "stale_if_fail": True,
            }
            failures.append(event)
            append_source_event(data_dir, event)
            print(f"failed {endpoint.source}/{endpoint.name}; preserving previous raw snapshot: {event['error']}")


def run_build_site(data_dir: Path, as_of: date, allow_validation_errors: bool = False) -> int:
    snapshots = load_latest_snapshots(data_dir)
    if not snapshots:
        raise SystemExit(f"No raw snapshots found under {data_dir / 'raw'}")
    return write_outputs(data_dir, snapshots, as_of, allow_validation_errors)


def run_validate(data_dir: Path, as_of: date, allow_validation_errors: bool = False) -> int:
    processed_path = data_dir / "processed" / "ipos.json"
    if not processed_path.exists():
        raise SystemExit(f"Missing {processed_path}; run fetch or build-site first.")
    records = json.loads(processed_path.read_text(encoding="utf-8"))
    report = validate_records(records, as_of)
    write_json(data_dir / "reports" / "validation.json", report)
    print(f"validated {report['record_count']} records: {report['error_count']} errors, {report['warning_count']} warnings")
    return 0 if allow_validation_errors else 1 if report["error_count"] else 0


def write_outputs(data_dir: Path, snapshots: list[dict], as_of: date, allow_validation_errors: bool = False) -> int:
    records = normalize_snapshots(snapshots, as_of)
    report = validate_records(records, as_of)
    quarantined_ids = set()
    for error in report["errors"]:
        if error.get("id"):
            quarantined_ids.add(error["id"])
        for record_id in error.get("ids", []):
            quarantined_ids.add(record_id)
    publishable_records = [record for record in records if record.get("id") not in quarantined_ids]
    quarantined_records = [record for record in records if record.get("id") in quarantined_ids]
    site_records = merge_for_site(publishable_records, as_of)

    write_json(data_dir / "processed" / "ipos.json", records)
    write_json(data_dir / "site" / "ipos.json", site_records)
    write_json(data_dir / "reports" / "validation.json", report)
    write_json(data_dir / "reports" / "quarantine.json", quarantined_records)
    manifest = build_astro_site_data(data_dir / "site", site_records, report, as_of, snapshots)

    print(f"normalized {len(records)} records")
    print(f"wrote {len(site_records)} site records")
    print(f"wrote Astro data bundle with {manifest['counts']['issues']} issues and {manifest['counts']['companies']} companies")
    print(f"quarantined {len(quarantined_records)} records")
    print(f"validation: {report['error_count']} errors, {report['warning_count']} warnings")
    return 0 if allow_validation_errors else 1 if report["error_count"] else 0
