"""CLI entry point for the v2 rebuild orchestrator.

Usage
-----
    python -m ipo_portal.orchestrator catalog [--only NAME] [--limit N] [--model MODEL]
    python -m ipo_portal.orchestrator schema  (planned)
    python -m ipo_portal.orchestrator audit   (planned)
    python -m ipo_portal.orchestrator normalize (planned)
    python -m ipo_portal.orchestrator gap-scan  (planned)
    python -m ipo_portal.orchestrator enrich-rhp (planned)

Each phase is incremental: catalog feeds schema, schema + audit feed
normalize, gap-scan reads catalog to find missing fields, enrich-rhp adds
prospectus extraction. Phases are safe to rerun; outputs are hash-gated
and DeepSeek calls are disk-cached by input hash.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .academy import (
    DEFAULT_ANTI_SLOP_SPEC_PATH as ACADEMY_ANTI_SLOP_PATH,
    DEFAULT_RUNS_ROOT as ACADEMY_RUNS_ROOT,
    DEFAULT_SOURCES_ROOT as ACADEMY_SOURCES_ROOT,
    PIPELINE_STAGES as ACADEMY_STAGES,
    run_pipeline as run_academy_pipeline,
)
from .audit import (
    DEFAULT_REPORT_PATH as AUDIT_REPORT_PATH,
    DEFAULT_RULES_PATH as AUDIT_RULES_PATH,
    DEFAULT_SITE_ROOT as AUDIT_SITE_ROOT,
    run_audit,
)
from .catalog import catalog_all
from .drift import DRIFT_LOG_DEFAULT, scan_drift
from .gap_scan import (
    DEFAULT_CATALOG_ROOT as GAP_CATALOG_ROOT,
    DEFAULT_REPORT_PATH as GAP_REPORT_PATH,
    DEFAULT_SITE_ROOT as GAP_SITE_ROOT,
    scan_gaps,
)
from .schema_design import CANONICAL_TARGETS, design_schemas


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_RAW_ROOT = PROJECT_ROOT / "data" / "raw"
DEFAULT_CATALOG_ROOT = PROJECT_ROOT / "docs" / "schema" / "raw_catalog"
DEFAULT_DRIFT_LOG = PROJECT_ROOT / DRIFT_LOG_DEFAULT
DEFAULT_SCHEMA_OUT = PROJECT_ROOT / "docs" / "schema" / "v2"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ipo_portal.orchestrator",
        description="DeepSeek-driven orchestrator for the v2 IPO database rebuild.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    catalog = sub.add_parser(
        "catalog",
        help="Phase 1: catalog every raw endpoint with DeepSeek",
        description=(
            "Walks data/raw/, picks one representative snapshot per (source, "
            "endpoint_group), and asks DeepSeek to produce a machine-readable "
            "field catalogue. Output goes to docs/schema/raw_catalog/."
        ),
    )
    catalog.add_argument(
        "--raw-root",
        type=Path,
        default=DEFAULT_RAW_ROOT,
        help="Root of raw snapshots (default: data/raw/).",
    )
    catalog.add_argument(
        "--out-root",
        type=Path,
        default=DEFAULT_CATALOG_ROOT,
        help="Output directory (default: docs/schema/raw_catalog/).",
    )
    catalog.add_argument(
        "--only",
        action="append",
        default=None,
        help=(
            "Restrict to a specific endpoint_group or concrete endpoint name. "
            "Repeatable. Useful for piloting on one endpoint."
        ),
    )
    catalog.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of endpoints processed.",
    )
    catalog.add_argument(
        "--model",
        default="deepseek-chat",
        help="DeepSeek model alias (default: deepseek-chat).",
    )

    drift_p = sub.add_parser(
        "drift",
        help="Detect upstream schema drift vs the Phase 1 catalog",
        description=(
            "Walks the freshest snapshot per (source, endpoint_group) and "
            "diffs leaf-field paths against docs/schema/raw_catalog/. New "
            "fields, removed fields, and incompatible type changes are "
            "logged to data/reports/upstream_drift.jsonl. Exits non-zero if "
            "any removed-path drift is observed."
        ),
    )
    drift_p.add_argument(
        "--raw-root", type=Path, default=DEFAULT_RAW_ROOT,
        help="Root of raw snapshots.",
    )
    drift_p.add_argument(
        "--catalog-root", type=Path, default=DEFAULT_CATALOG_ROOT,
        help="Root of the Phase 1 catalog.",
    )
    drift_p.add_argument(
        "--drift-log", type=Path, default=DEFAULT_DRIFT_LOG,
        help="JSONL log of drift events (append-only).",
    )

    schema_p = sub.add_parser(
        "schema",
        help="Phase 2: synthesize canonical v2 JSON Schemas",
        description=(
            "Aggregates per-endpoint catalogs from Phase 1 into a corpus of "
            "field hints, asks DeepSeek to design canonical JSON Schemas for "
            "issue/company/trajectory, and writes them to docs/schema/v2/."
        ),
    )
    schema_p.add_argument("--catalog-root", type=Path, default=DEFAULT_CATALOG_ROOT)
    schema_p.add_argument("--out-root", type=Path, default=DEFAULT_SCHEMA_OUT)
    schema_p.add_argument("--target", action="append", default=None, choices=list(CANONICAL_TARGETS))
    schema_p.add_argument("--model", default="deepseek-chat")

    audit_p = sub.add_parser(
        "audit",
        help="Phase 3: pollution / collision audit against existing site data",
        description=(
            "Samples records from the live data/site/ tree, asks DeepSeek to "
            "identify contamination patterns and propose dedup/precedence "
            "rules. Writes data/reports/pollution_audit.json and a starter "
            "docs/data/DEDUP_RULES.yaml."
        ),
    )
    audit_p.add_argument("--site-root", type=Path, default=PROJECT_ROOT / AUDIT_SITE_ROOT)
    audit_p.add_argument("--report", type=Path, default=PROJECT_ROOT / AUDIT_REPORT_PATH)
    audit_p.add_argument("--rules", type=Path, default=PROJECT_ROOT / AUDIT_RULES_PATH)
    audit_p.add_argument("--review-cap", type=int, default=200)
    audit_p.add_argument("--control-size", type=int, default=20)
    audit_p.add_argument("--model", default="deepseek-chat")

    gap_p = sub.add_parser(
        "gap-scan",
        help="Phase 5: surface canonical fields the catalog lists but v1 lacks",
        description=(
            "Cross-reference docs/schema/raw_catalog/ canonical_field_hint "
            "values against the current data/site/ records. Writes "
            "data/reports/gap_scan.json. No DeepSeek call required."
        ),
    )
    gap_p.add_argument("--catalog-root", type=Path, default=PROJECT_ROOT / GAP_CATALOG_ROOT)
    gap_p.add_argument("--site-root", type=Path, default=PROJECT_ROOT / GAP_SITE_ROOT)
    gap_p.add_argument("--report", type=Path, default=PROJECT_ROOT / GAP_REPORT_PATH)
    gap_p.add_argument("--v1-sample-limit", type=int, default=500)

    normalize_p = sub.add_parser(
        "normalize",
        help="Phase 4: build data/site_v2/",
        description=(
            "Runs the v2 normalizer end-to-end: collect contributions from "
            "registered parsers, merge by stable join key with precedence "
            "rules, wrap in metadata envelope, validate, route to "
            "issues/by-slug/ or quarantine/, write manifest. Hash-gated."
        ),
    )
    normalize_p.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    normalize_p.add_argument("--out-root", type=Path, default=PROJECT_ROOT / "data" / "site_v2")
    normalize_p.add_argument("--schema-root", type=Path, default=DEFAULT_SCHEMA_OUT)
    normalize_p.add_argument(
        "--precedence",
        type=Path,
        default=PROJECT_ROOT / "docs" / "data" / "SOURCE_PRECEDENCE.yaml",
    )

    academy_p = sub.add_parser(
        "academy",
        help="Academy: long-form content generation pipeline (outline → draft → factcheck → edit → visual)",
        description=(
            "Runs one or more pipeline stages for an Academy article. "
            "The article slug must already have a brief at "
            "data/academy/runs/<slug>/brief.json and the sources it "
            "references must be harvested into data/academy/sources/. "
            "See docs/decisions/003-academy-anti-slop-spec.md for the "
            "editorial bar."
        ),
    )
    academy_p.add_argument(
        "--slug",
        required=True,
        help="Article slug (matches the brief filename and the rendered MDX filename).",
    )
    academy_p.add_argument(
        "--from",
        dest="from_stage",
        default="outline",
        choices=list(ACADEMY_STAGES),
        help="First stage to run (default: outline).",
    )
    academy_p.add_argument(
        "--to",
        dest="to_stage",
        default="visual",
        choices=list(ACADEMY_STAGES),
        help="Last stage to run, inclusive (default: visual).",
    )
    academy_p.add_argument(
        "--runs-root",
        type=Path,
        default=ACADEMY_RUNS_ROOT,
        help="Override the per-article artifact root (default: data/academy/runs/).",
    )
    academy_p.add_argument(
        "--sources-root",
        type=Path,
        default=ACADEMY_SOURCES_ROOT,
        help="Override the harvested-source root (default: data/academy/sources/).",
    )
    academy_p.add_argument(
        "--anti-slop-spec",
        type=Path,
        default=ACADEMY_ANTI_SLOP_PATH,
        help="Override the anti-slop spec path (default: docs/decisions/003-academy-anti-slop-spec.md).",
    )
    academy_p.add_argument(
        "--allow-gaps",
        action="store_true",
        help="Proceed to draft even if the outline reports blocking gaps. Off by default.",
    )
    academy_p.add_argument(
        "--allow-blocking",
        action="store_true",
        help="Proceed to edit even if fact-check reports blocking issues. Off by default.",
    )

    enrich_p = sub.add_parser(
        "enrich-rhp",
        help="Phase 6: extract structured prospectus data from one RHP/DRHP PDF",
        description=(
            "Downloads an RHP/DRHP PDF, runs pdftotext, asks DeepSeek for "
            "page-cited extraction across 9 schema-targeted sections, "
            "writes data/site_v2/issues/<slug>/prospectus.json. "
            "Each leaf carries {value, raw_excerpt, source_page, "
            "source_section, confidence} so the website can show 'Source: "
            "RHP p.X' beside every fact. ~$0.15 per RHP, cached on disk."
        ),
    )
    enrich_p.add_argument(
        "--url",
        help="URL of the RHP/DRHP PDF to extract from (single-issue mode).",
    )
    enrich_p.add_argument(
        "--slug",
        help="Issue slug (single-issue mode; becomes data/site_v2/issues/<slug>/prospectus.json).",
    )
    enrich_p.add_argument(
        "--scan-pending",
        action="store_true",
        help=(
            "Discovery mode: walk data/site_v2/issues/by-slug/, find issues "
            "that are Open/Upcoming/Filed (or Listed within --recent-days) AND "
            "have an RHP/DRHP URL AND have no prospectus.json yet, then extract "
            "each. This is the go-forward cron hook (no backfill)."
        ),
    )
    enrich_p.add_argument(
        "--recent-days",
        type=int,
        default=45,
        help="With --scan-pending, also enrich issues Listed within this many days.",
    )
    enrich_p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="With --scan-pending, cap the number of issues extracted this run.",
    )
    enrich_p.add_argument(
        "--site-root",
        type=Path,
        default=PROJECT_ROOT / "data" / "site_v2",
        help="Root of the v2 site tree (default: data/site_v2/).",
    )

    refresh_p = sub.add_parser(
        "refresh",
        help="Daily/periodic refresh: SEBI + NSE/BSE fetch → normalize → enrich → drift",
        description=(
            "The cron entrypoint. Runs the full go-forward cycle with each "
            "step independently guarded. --hot runs a fast subset (SEBI + "
            "normalize + current-IPO enrichment) for frequent intraday runs; "
            "the full cycle (default) also fetches NSE/BSE and runs drift."
        ),
    )
    refresh_p.add_argument("--skip-fetch", action="store_true", help="Skip the NSE/BSE v1 fetch.")
    refresh_p.add_argument("--skip-sebi", action="store_true", help="Skip the SEBI scrape.")
    refresh_p.add_argument("--skip-kite", action="store_true", help="Skip Kite market data and rely on Yahoo fallback.")
    refresh_p.add_argument("--skip-yahoo", action="store_true", help="Skip Yahoo market data.")
    refresh_p.add_argument("--skip-tijori", action="store_true", help="Skip Tijori IPO screener enrichment.")
    refresh_p.add_argument("--skip-enrich", action="store_true", help="Skip RHP enrichment.")
    refresh_p.add_argument("--hot", action="store_true", help="Fast subset for frequent runs.")
    refresh_p.add_argument("--enrich-limit", type=int, default=25, help="Cap RHP extractions per run.")

    refresh_daily_p = sub.add_parser(
        "refresh-daily",
        help="V3 daily refresh alias: public sources → normalize → export-v3 → validation.",
        description=(
            "Runs the V3 daily refresh path. This is an explicit cron-friendly "
            "alias around the guarded full refresh cycle and writes "
            "data/ipo_watch_v3 as the public dataset."
        ),
    )
    refresh_daily_p.add_argument("--skip-fetch", action="store_true", help="Skip NSE/BSE fetch.")
    refresh_daily_p.add_argument("--skip-sebi", action="store_true", help="Skip SEBI scrape.")
    refresh_daily_p.add_argument("--skip-kite", action="store_true", help="Skip Kite market data and rely on Yahoo fallback.")
    refresh_daily_p.add_argument("--skip-yahoo", action="store_true", help="Skip Yahoo market data; use the latest committed Yahoo raw snapshot.")
    refresh_daily_p.add_argument("--skip-tijori", action="store_true", help="Skip Tijori IPO screener enrichment.")
    refresh_daily_p.add_argument("--skip-enrich", action="store_true", help="Skip legacy RHP enrichment.")
    refresh_daily_p.add_argument("--enrich-limit", type=int, default=25, help="Cap legacy RHP extractions per run.")

    new_filings_p = sub.add_parser(
        "refresh-new-filings",
        help="SEBI evening job: turn fresh public-issue filings into citation-backed articles.",
        description=(
            "Fetches the latest SEBI public issue filings, resolves PDFs, "
            "downloads new documents, extracts citation-validated facts with "
            "Gemini, and writes data/ipo_watch_v3/new_filings/ for the site."
        ),
    )
    new_filings_p.add_argument("--limit", type=int, default=25, help="Latest SEBI filing rows to inspect.")
    new_filings_p.add_argument("--process-limit", type=int, default=5, help="Maximum new PDFs to process this run.")
    new_filings_p.add_argument("--model", default="gemini-3.1-flash-lite", help="Primary Gemini model.")
    new_filings_p.add_argument("--retry-model", default="gemini-3-flash-preview", help="Retry model for failed quality; use 'none' to disable.")
    new_filings_p.add_argument("--force", action="store_true", help="Reprocess existing matching articles.")
    new_filings_p.add_argument("--dry-run", action="store_true", help="Fetch SEBI and list work without model calls or writes.")
    new_filings_p.add_argument("--quality-gate", action="store_true", help="Exit non-zero if any processed filing is quarantined or failed.")

    refresh_subs_p = sub.add_parser(
        "refresh-subscriptions",
        help="High-frequency V3 subscription refresh for active IPOs only.",
        description=(
            "Fetches fresh exchange data, normalizes staging data, and updates "
            "only active issue subscription artifacts, matching index summary "
            "fields, and trajectories in data/ipo_watch_v3."
        ),
    )
    refresh_subs_p.add_argument("--skip-fetch", action="store_true", help="Use existing raw snapshots; do not fetch NSE/BSE.")

    refresh_yahoo_p = sub.add_parser(
        "refresh-yahoo-market",
        help="Standalone Yahoo market-data refresh.",
        description=(
            "Reads committed V3 issue records, fetches Yahoo listing/current "
            "prices, and writes data/raw/yahoo plus a refresh report. The "
            "public V3 rebuild remains the daily reconciliation job."
        ),
    )
    refresh_yahoo_p.add_argument("--site-root", type=Path, default=PROJECT_ROOT / "data" / "ipo_watch_v3")
    refresh_yahoo_p.add_argument("--data-root", type=Path, default=PROJECT_ROOT / "data")
    refresh_yahoo_p.add_argument("--limit", type=int, default=None)
    refresh_yahoo_p.add_argument("--sleep-seconds", type=float, default=0.15)
    refresh_yahoo_p.add_argument("--cache-max-age-hours", type=float, default=18.0)

    refresh_tijori_p = sub.add_parser(
        "refresh-tijori-enrichment",
        help="Standalone Tijori IPO screener enrichment refresh.",
        description=(
            "Fetches the Tijori IPO screener feed, stores the raw snapshot, "
            "and refreshes data/derived/tijori_ipo_enrichment.json plus the "
            "sector map for the next V3 daily build."
        ),
    )
    refresh_tijori_p.add_argument("--data-root", type=Path, default=PROJECT_ROOT / "data")

    source_audit_p = sub.add_parser(
        "audit-source-structure",
        help="Audit primary NSE/BSE/SEBI source coverage for V3.",
        description=(
            "Reads data/ipo_watch_v3/_meta/source_coverage.json and writes a "
            "primary source structure report highlighting parser/fetch gaps "
            "that block canonical NSE/BSE/SEBI coverage."
        ),
    )
    source_audit_p.add_argument("--site-root", type=Path, default=PROJECT_ROOT / "data" / "ipo_watch_v3")
    source_audit_p.add_argument("--report", type=Path, default=PROJECT_ROOT / "data" / "reports" / "primary_source_structure_audit.json")
    source_audit_p.add_argument("--gate", action="store_true", help="Exit non-zero when blocking primary source gaps remain.")

    yahoo_p = sub.add_parser(
        "yahoo-performance",
        help="Fetch Yahoo Finance listing/current prices into data/raw/yahoo/performance.",
        description=(
            "Reads data/site_v2 issues, queries Yahoo chart data for IPOs with "
            "symbol + listing_date + issue_price, and writes a raw snapshot. "
            "Run normalize afterwards to merge the prices with provenance."
        ),
    )
    yahoo_p.add_argument("--site-root", type=Path, default=PROJECT_ROOT / "data" / "site_v2")
    yahoo_p.add_argument("--data-root", type=Path, default=PROJECT_ROOT / "data")
    yahoo_p.add_argument("--cache-dir", type=Path, default=PROJECT_ROOT / "data" / "cache" / "yahoo")
    yahoo_p.add_argument("--cache-max-age-hours", type=float, default=18.0)
    yahoo_p.add_argument("--no-cache", action="store_true", help="Do not read/write the Yahoo response cache.")
    yahoo_p.add_argument("--limit", type=int, default=None, help="Cap queried issues for a trial run.")
    yahoo_p.add_argument("--sleep-seconds", type=float, default=0.15, help="Delay between Yahoo requests.")

    v3_p = sub.add_parser(
        "export-v3",
        help="Build the self-contained data/ipo_watch_v3 public dataset from cleaned V2.",
        description=(
            "Materializes data/ipo_watch_v3/ as the website-facing public contract. "
            "The output includes all issues, indexes, companies, trajectories, "
            "available prospectus extractions, schema metadata, and app-native "
            "/ipos/ + /companies/ URL paths. It has no runtime dependency on "
            "data/site_v2."
        ),
    )
    v3_p.add_argument("--source-root", type=Path, default=PROJECT_ROOT / "data" / "site_v2")
    v3_p.add_argument("--out-root", type=Path, default=PROJECT_ROOT / "data" / "ipo_watch_v3")
    v3_p.add_argument("--schema-root", type=Path, default=PROJECT_ROOT / "docs" / "schema" / "v3")

    sectors_p = sub.add_parser(
        "classify-sectors",
        help="Bulk sector/industry classification (DeepSeek) for issues lacking a sector",
        description=(
            "Walks data/site_v2/issues/by-slug/, classifies companies with "
            "no sector into a fixed sector vocabulary via DeepSeek, writes "
            "data/derived/sector_map.json. Run normalize afterwards to apply."
        ),
    )
    sectors_p.add_argument("--limit", type=int, default=None, help="Cap issues classified this run.")
    sectors_p.add_argument("--model", default="deepseek-chat")

    filings_p = sub.add_parser(
        "process-filings-v3",
        help="Extract citation-verified V3 prospectus facts from primary filing PDFs.",
        description=(
            "Scans data/ipo_watch_v3 for RHP/DRHP/prospectus URLs, downloads PDFs, "
            "extracts facts in either text or direct-PDF mode, and publishes "
            "only citation-verified facts to issues/<slug>/prospectus_facts.json."
        ),
    )
    filings_p.add_argument("--site-root", type=Path, default=PROJECT_ROOT / "data" / "ipo_watch_v3")
    filings_p.add_argument("--slug", help="Process one issue slug.")
    filings_p.add_argument("--url", help="Process one explicit filing URL; requires --slug.")
    filings_p.add_argument("--document-type", default="RHP", help="RHP, DRHP, prospectus, NCD prospectus, etc.")
    filings_p.add_argument("--limit", type=int, default=None, help="Limit scanned jobs.")
    filings_p.add_argument("--provider", choices=["deepseek", "openrouter"], default="deepseek")
    filings_p.add_argument("--model", default=None, help="Model id. Defaults to provider default.")
    filings_p.add_argument(
        "--input-mode",
        choices=["text", "pdf", "pdf-url", "pdf-base64"],
        default="text",
        help=(
            "Model input path. text uses pdftotext slices; pdf sends direct public PDF URLs when possible "
            "and base64 for cached/ZIP PDFs; pdf-url requires a direct public PDF URL; pdf-base64 always "
            "sends the downloaded cached PDF."
        ),
    )
    filings_p.add_argument(
        "--pdf-engine",
        choices=["native", "cloudflare-ai", "mistral-ocr", "none"],
        default="native",
        help="OpenRouter PDF parser mode for PDF input. native is best for Gemini/VL; none omits plugin config.",
    )
    filings_p.add_argument(
        "--text-extractor",
        choices=["pdftotext", "liteparse"],
        default="pdftotext",
        help=(
            "Local PDF text backend used for text-mode prompts and citation verification. "
            "liteparse is optional and requires requirements-extractors.txt."
        ),
    )
    filings_p.add_argument("--dry-run", action="store_true", help="Download and text-extract only; no DeepSeek call and no write.")
    filings_p.add_argument("--list", action="store_true", help="List discovered filing jobs and exit.")
    filings_p.add_argument("--force", action="store_true", help="Reprocess filings even when a current pass/review extraction already exists.")
    filings_p.add_argument(
        "--benchmark-models",
        help="Comma-separated model IDs to compare on one section without writing prospectus facts.",
    )
    filings_p.add_argument("--benchmark-section", default="business")
    filings_p.add_argument("--benchmark-max-chars", type=int, default=30000)
    filings_p.add_argument(
        "--quality-gate",
        action="store_true",
        help="Exit non-zero if any processed filing has quality.state=fail.",
    )
    filings_p.add_argument(
        "--strict-quality-gate",
        action="store_true",
        help="Exit non-zero if any processed filing has quality.state=fail or review.",
    )

    filings_alias_p = sub.add_parser(
        "process-filings",
        help="Alias for process-filings-v3.",
        description="Extract citation-verified V3 prospectus facts from primary filing PDFs.",
    )
    for action in filings_p._actions:
        if action.dest == "help":
            continue
        flags = [*action.option_strings]
        if not flags:
            continue
        kwargs = {
            "dest": action.dest,
            "default": action.default,
            "help": action.help,
            "required": getattr(action, "required", False),
        }
        if getattr(action, "choices", None) is not None:
            kwargs["choices"] = action.choices
        if getattr(action, "type", None) is not None:
            kwargs["type"] = action.type
        if action.nargs is not None:
            kwargs["nargs"] = action.nargs
        if isinstance(action, argparse._StoreTrueAction):
            kwargs.pop("type", None)
            kwargs.pop("nargs", None)
            filings_alias_p.add_argument(*flags, action="store_true", help=action.help)
        else:
            filings_alias_p.add_argument(*flags, **kwargs)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "catalog":
        return _run_catalog(args)
    if args.command == "drift":
        return _run_drift(args)
    if args.command == "schema":
        return _run_schema(args)
    if args.command == "audit":
        return _run_audit(args)
    if args.command == "gap-scan":
        return _run_gap_scan(args)
    if args.command == "normalize":
        return _run_normalize(args)
    if args.command == "enrich-rhp":
        return _run_enrich_rhp(args)
    if args.command == "refresh":
        return _run_refresh(args)
    if args.command == "refresh-daily":
        return _run_refresh_daily(args)
    if args.command == "refresh-new-filings":
        return _run_refresh_new_filings(args)
    if args.command == "refresh-subscriptions":
        return _run_refresh_subscriptions(args)
    if args.command == "refresh-yahoo-market":
        return _run_refresh_yahoo_market(args)
    if args.command == "refresh-tijori-enrichment":
        return _run_refresh_tijori_enrichment(args)
    if args.command == "audit-source-structure":
        return _run_audit_source_structure(args)
    if args.command == "yahoo-performance":
        return _run_yahoo_performance(args)
    if args.command == "export-v3":
        return _run_export_v3(args)
    if args.command == "classify-sectors":
        from .sectors import classify

        stats = classify(limit=args.limit, model=args.model)
        print(f"[classify-sectors] {stats}")
        return 0
    if args.command == "academy":
        return _run_academy(args)
    if args.command in {"process-filings-v3", "process-filings"}:
        return _run_process_filings_v3(args)

    print(f"[orchestrator] '{args.command}' is not implemented yet — see roadmap in __init__.py.")
    return 1


def _run_catalog(args: argparse.Namespace) -> int:
    raw_root: Path = args.raw_root
    out_root: Path = args.out_root
    if not raw_root.exists():
        print(f"[catalog] raw root not found: {raw_root}")
        return 2

    results = catalog_all(
        raw_root=raw_root,
        out_root=out_root,
        model=args.model,
        only=args.only,
        limit=args.limit,
    )

    if not results:
        print("[catalog] no endpoints matched. (Wrong --only filter?)")
        return 3

    total_tokens_in = sum(r.tokens_in for r in results)
    total_tokens_out = sum(r.tokens_out for r in results)
    total_cost = sum(r.cost_usd for r in results)
    cached_n = sum(1 for r in results if r.cached)
    print(
        f"[catalog] cataloged {len(results)} endpoints "
        f"({cached_n} from cache). "
        f"tokens in={total_tokens_in} out={total_tokens_out} cost≈${total_cost:.4f}"
    )
    for r in results:
        marker = " (cached)" if r.cached else ""
        print(f"  - {r.sample.source}/{r.sample.endpoint_group}{marker} -> {r.output_path}")
    return 0


def _run_schema(args: argparse.Namespace) -> int:
    targets = tuple(args.target) if args.target else CANONICAL_TARGETS
    index_path = design_schemas(
        catalog_root=args.catalog_root,
        schema_root=args.out_root,
        targets=targets,
        model=args.model,
    )
    print(f"[schema] design summary written to {index_path}")
    print(f"[schema] targets: {targets}")
    return 0


def _run_audit(args: argparse.Namespace) -> int:
    report_path = run_audit(
        site_root=args.site_root,
        report_path=args.report,
        rules_path=args.rules,
        review_cap=args.review_cap,
        control_size=args.control_size,
        model=args.model,
    )
    print(f"[audit] report written to {report_path}")
    print(f"[audit] starter dedup rules at {args.rules}")
    return 0


def _run_gap_scan(args: argparse.Namespace) -> int:
    report_path = scan_gaps(
        catalog_root=args.catalog_root,
        site_root=args.site_root,
        report_path=args.report,
        v1_sample_limit=args.v1_sample_limit,
    )
    print(f"[gap-scan] report written to {report_path}")
    return 0


def _run_normalize(args: argparse.Namespace) -> int:
    from ..normalize_v2.pipeline import run_normalize

    out_root = run_normalize(
        raw_root=args.raw_root,
        out_root=args.out_root,
        schema_root=args.schema_root,
        precedence_path=args.precedence,
    )
    manifest = args.out_root / "manifest.json"
    print(f"[normalize] data/site_v2/ written to {out_root}")
    print(f"[normalize] manifest: {manifest}")
    return 0


def _load_rhp_extractor():
    """Load scripts/extract_rich_rhp.py as a module (avoids code duplication)."""
    import importlib.util
    import sys

    script = PROJECT_ROOT / "scripts" / "extract_rich_rhp.py"
    spec = importlib.util.spec_from_file_location("extract_rich_rhp", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {script}")
    module = importlib.util.module_from_spec(spec)
    # Register before exec so @dataclass decorators resolve their module.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run_refresh(args: argparse.Namespace) -> int:
    from .refresh import run_refresh

    summary = run_refresh(
        skip_fetch=args.skip_fetch,
        skip_sebi=args.skip_sebi,
        skip_kite=args.skip_kite,
        skip_yahoo=args.skip_yahoo,
        skip_tijori=args.skip_tijori,
        skip_enrich=args.skip_enrich,
        hot=args.hot,
        enrich_limit=args.enrich_limit,
    )
    print(f"[refresh] cycle complete — ok={summary['ok']}, mode={summary['mode']}")
    for step in summary["steps"]:
        print(f"  {step['name']:16s} {step['status']:8s} {step['elapsed_ms']:>7} ms")
    return 0 if summary["ok"] else 1


def _run_refresh_daily(args: argparse.Namespace) -> int:
    from .refresh import run_refresh

    summary = run_refresh(
        skip_fetch=args.skip_fetch,
        skip_sebi=args.skip_sebi,
        skip_kite=args.skip_kite,
        skip_yahoo=args.skip_yahoo,
        skip_tijori=args.skip_tijori,
        skip_enrich=args.skip_enrich,
        hot=False,
        enrich_limit=args.enrich_limit,
    )
    print(f"[refresh-daily] cycle complete — ok={summary['ok']}")
    for step in summary["steps"]:
        print(f"  {step['name']:22s} {step['status']:8s} {step['elapsed_ms']:>7} ms")
    return 0 if summary["ok"] else 1


def _run_refresh_subscriptions(args: argparse.Namespace) -> int:
    from .refresh import run_subscription_refresh

    summary = run_subscription_refresh(skip_fetch=args.skip_fetch)
    print(f"[refresh-subscriptions] cycle complete — ok={summary['ok']}")
    for step in summary["steps"]:
        print(f"  {step['name']:22s} {step['status']:8s} {step['elapsed_ms']:>7} ms")
    return 0 if summary["ok"] else 1


def _run_refresh_yahoo_market(args: argparse.Namespace) -> int:
    from .refresh import run_yahoo_market_refresh

    summary = run_yahoo_market_refresh(
        site_root=args.site_root,
        data_root=args.data_root,
        limit=args.limit,
        sleep_seconds=args.sleep_seconds,
        cache_max_age_hours=args.cache_max_age_hours,
    )
    print(f"[refresh-yahoo-market] cycle complete — ok={summary['ok']}")
    for step in summary["steps"]:
        print(f"  {step['name']:22s} {step['status']:8s} {step['elapsed_ms']:>7} ms")
    return 0 if summary["ok"] else 1


def _run_refresh_tijori_enrichment(args: argparse.Namespace) -> int:
    from .refresh import run_tijori_enrichment_refresh

    summary = run_tijori_enrichment_refresh(data_root=args.data_root)
    print(f"[refresh-tijori-enrichment] cycle complete — ok={summary['ok']}")
    for step in summary["steps"]:
        print(f"  {step['name']:22s} {step['status']:8s} {step['elapsed_ms']:>7} ms")
    return 0 if summary["ok"] else 1


def _run_refresh_new_filings(args: argparse.Namespace) -> int:
    from ..new_filings import refresh_new_filings

    retry_model = None if str(args.retry_model).lower() in {"", "none", "null", "off"} else args.retry_model
    summary = refresh_new_filings(
        limit=args.limit,
        process_limit=args.process_limit,
        model=args.model,
        retry_model=retry_model,
        force=args.force,
        dry_run=args.dry_run,
    )
    print(
        "[refresh-new-filings] "
        f"fetched={summary['fetched']} eligible={summary['eligible']} "
        f"processed={summary['processed']} skipped={summary['skipped_existing']} "
        f"published={summary['published']} quarantined={summary['quarantined']} failed={summary['failed']}"
    )
    if summary.get("failures"):
        for failure in summary["failures"][:10]:
            print(f"  FAILED {failure.get('slug')}: {failure.get('error')}")
    if summary["failed"]:
        return 1
    if args.quality_gate and summary["processed"] and (summary["quarantined"] or summary["failed"]):
        return 1
    return 0


def _run_audit_source_structure(args: argparse.Namespace) -> int:
    import importlib.util
    import sys

    script = PROJECT_ROOT / "scripts" / "audit_primary_source_structure.py"
    spec = importlib.util.spec_from_file_location("audit_primary_source_structure", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    argv = ["--site-root", str(args.site_root), "--report", str(args.report)]
    if args.gate:
        argv.append("--gate")
    old_argv = sys.argv
    try:
        sys.argv = [str(script), *argv]
        return int(module.main())
    finally:
        sys.argv = old_argv


def _run_yahoo_performance(args: argparse.Namespace) -> int:
    from ..yahoo_v2 import export_snapshot

    path = export_snapshot(
        site_root=args.site_root,
        data_root=args.data_root,
        limit=args.limit,
        sleep_seconds=args.sleep_seconds,
        cache_dir=None if args.no_cache else args.cache_dir,
        cache_max_age_hours=args.cache_max_age_hours,
    )
    print(f"[yahoo-performance] wrote snapshot: {path}")
    print("[yahoo-performance] run `python -m ipo_portal.orchestrator normalize` to merge it.")
    return 0


def _run_export_v3(args: argparse.Namespace) -> int:
    from ..site_v3 import export_v3

    stats = export_v3(
        source_root=args.source_root,
        out_root=args.out_root,
        schema_root=args.schema_root,
    )
    print(f"[export-v3] wrote self-contained V3 tree: {stats.out_root}")
    print(
        "[export-v3] "
        f"issues={stats.issues} companies={stats.companies} "
        f"trajectories={stats.trajectories} prospectuses={stats.prospectuses} "
        f"version={stats.dataset_version}"
    )
    return 0


def _run_enrich_rhp(args: argparse.Namespace) -> int:
    module = _load_rhp_extractor()

    if args.scan_pending:
        return _run_enrich_scan(args, module)

    if not args.url or not args.slug:
        print("[enrich-rhp] need --url and --slug (single mode) or --scan-pending.")
        return 2
    doc = module.run(args.url, args.slug)
    meta = doc.get("meta", {})
    print(f"[enrich-rhp] extracted {meta.get('rhp_pdf_pages')} pages, "
          f"{meta.get('raw_text_chars', 0):,} chars, "
          f"cost=${meta.get('total_cost_usd', 0):.4f}")
    print(f"[enrich-rhp] written to data/site_v2/issues/{args.slug}/prospectus.json")
    return 0


def _run_process_filings_v3(args: argparse.Namespace) -> int:
    import json

    from ..filing_processor import FilingJob, benchmark_models, discover_jobs, process_filing

    if args.url:
        if not args.slug:
            print("[process-filings-v3] --url requires --slug")
            return 2
        jobs = [FilingJob(slug=args.slug, url=args.url, document_type=args.document_type, source="manual")]
    else:
        jobs = discover_jobs(args.site_root, limit=args.limit, include_existing=args.force)
        if args.slug:
            jobs = [job for job in jobs if job.slug == args.slug]

    if args.list:
        for job in jobs:
            print(f"{job.slug}\t{job.document_type}\t{job.url}")
        print(f"[process-filings-v3] jobs={len(jobs)}")
        return 0
    if not jobs:
        print("[process-filings-v3] no filing jobs found")
        return 0

    if args.benchmark_models:
        models = [item.strip() for item in args.benchmark_models.split(",") if item.strip()]
        report = benchmark_models(
            jobs[0],
            models=models,
            provider=args.provider,
            section_name=args.benchmark_section,
            max_chars=args.benchmark_max_chars,
            input_mode=args.input_mode,
            pdf_engine=None if args.pdf_engine == "none" else args.pdf_engine,
            text_extractor=args.text_extractor,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if all(row.get("ok") for row in report["models"]) else 1

    ok = 0
    failed = 0
    quality_failed = 0
    quality_review = 0
    for job in jobs:
        try:
            doc = process_filing(
                job,
                model=args.model,
                provider=args.provider,
                dry_run=args.dry_run,
                input_mode=args.input_mode,
                pdf_engine=None if args.pdf_engine == "none" else args.pdf_engine,
                text_extractor=args.text_extractor,
            )
            print(
                f"[process-filings-v3] {job.slug}: {doc.get('extraction_status')} "
                f"extractor={doc.get('pdf_text_extractor')} pages={doc.get('pdf_pages')} "
                f"chars={doc.get('pdf_text_chars')}"
            )
            quality = doc.get("quality") or {}
            if quality:
                print(
                    "[process-filings-v3] "
                    f"  quality={quality.get('state')} verified={quality.get('verified_fact_count')} "
                    f"redaction_rate={quality.get('redaction_rate')} repair_rate={quality.get('repair_rate')} "
                    f"missing={','.join(quality.get('missing_sections') or []) or '-'}"
                )
                if quality.get("state") == "fail":
                    quality_failed += 1
                elif quality.get("state") == "review":
                    quality_review += 1
            ok += 1
        except Exception as exc:  # noqa: BLE001 - batch should continue
            print(f"[process-filings-v3] FAILED {job.slug}: {type(exc).__name__}: {exc}")
            failed += 1
    print(f"[process-filings-v3] ok={ok} failed={failed} quality_failed={quality_failed} quality_review={quality_review}")
    if failed:
        return 1
    if args.strict_quality_gate and (quality_failed or quality_review):
        return 1
    if args.quality_gate and quality_failed:
        return 1
    return 0


def _run_enrich_scan(args: argparse.Namespace, module) -> int:
    """Discover and enrich pending issues (go-forward, no backfill)."""
    import json
    from datetime import date, timedelta

    site_root: Path = args.site_root
    by_slug = site_root / "issues" / "by-slug"
    if not by_slug.exists():
        print(f"[enrich-rhp] no by-slug dir at {by_slug}; run normalize first.")
        return 2

    recent_cutoff = (date.today() - timedelta(days=args.recent_days)).isoformat()

    # Go-forward, no backfill (the user's directive): only enrich issues
    # that are genuinely current. "Filed" status alone is NOT eligible —
    # there are ~1,600 historical document-only records in that state.
    # A Filed/Listed record qualifies only if its relevant date is recent.
    pending: list[tuple[str, str]] = []
    skipped_stale = 0
    for path in sorted(by_slug.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        slug = doc.get("slug")
        identity = doc.get("identity") or {}
        status = identity.get("status")
        docs = doc.get("documents") or {}
        timeline = doc.get("timeline") or {}
        rhp = docs.get("rhp_url") or docs.get("drhp_url")
        if not slug or not rhp:
            continue

        eligible = False
        if status in ("Open", "Upcoming"):
            eligible = True
        elif status == "Listed" and (timeline.get("listing_date") or "") >= recent_cutoff:
            eligible = True
        elif status == "Filed":
            # Only recently-filed DRHPs (e.g., from SEBI), never the
            # historical document-only backlog.
            recent_date = (
                timeline.get("drhp_filing_date")
                or timeline.get("open_date")
                or ""
            )
            if recent_date >= recent_cutoff:
                eligible = True
            else:
                skipped_stale += 1

        if not eligible:
            continue
        # Skip if already extracted.
        if (site_root / "issues" / slug / "prospectus.json").exists():
            continue
        pending.append((slug, rhp))

    if skipped_stale:
        print(f"[enrich-rhp] skipped {skipped_stale} stale Filed record(s) (no backfill).")

    if args.limit is not None:
        pending = pending[: args.limit]

    if not pending:
        print("[enrich-rhp] no pending issues to enrich (all current RHPs extracted).")
        return 0

    print(f"[enrich-rhp] {len(pending)} pending issue(s) to enrich:")
    total_cost = 0.0
    for slug, url in pending:
        print(f"[enrich-rhp] → {slug}")
        try:
            doc = module.run(url, slug)
        except Exception as exc:  # noqa: BLE001 — log and continue the batch
            print(f"[enrich-rhp]   FAILED {slug}: {exc!r}")
            continue
        total_cost += doc.get("meta", {}).get("total_cost_usd", 0.0)
    print(f"[enrich-rhp] done. approx cost this run: ${total_cost:.4f}")
    return 0


def _run_drift(args: argparse.Namespace) -> int:
    raw_root: Path = args.raw_root
    if not raw_root.exists():
        print(f"[drift] raw root not found: {raw_root}")
        return 2
    report = scan_drift(
        raw_root=raw_root,
        catalog_root=args.catalog_root,
        drift_log=args.drift_log,
    )
    added = sum(1 for e in report.events if e.kind == "added")
    removed = sum(1 for e in report.events if e.kind == "removed")
    changed = sum(1 for e in report.events if e.kind == "type_changed")
    print(
        f"[drift] inspected={report.inspected} "
        f"added={added} removed={removed} type_changed={changed} "
        f"missing_catalog={len(report.missing_catalog)} "
        f"log={args.drift_log}"
    )
    return 1 if report.blocking else 0


def _run_academy(args: argparse.Namespace) -> int:
    try:
        results = run_academy_pipeline(
            slug=args.slug,
            from_stage=args.from_stage,
            to_stage=args.to_stage,
            runs_root=args.runs_root,
            sources_root=args.sources_root,
            anti_slop_path=args.anti_slop_spec,
            allow_gaps=args.allow_gaps,
            allow_blocking=args.allow_blocking,
        )
    except FileNotFoundError as exc:
        print(f"[academy] {exc}")
        return 2
    except RuntimeError as exc:
        print(f"[academy] {exc}")
        return 3

    total_in = sum(r.tokens_in for r in results)
    total_out = sum(r.tokens_out for r in results)
    total_cost = sum(r.cost_usd for r in results)
    cached_n = sum(1 for r in results if r.cached)
    print(
        f"[academy] ran {len(results)} stage(s) for slug={args.slug!r} "
        f"({cached_n} from cache). "
        f"tokens in={total_in} out={total_out} cost≈${total_cost:.4f}"
    )
    for r in results:
        marker = " (cached)" if r.cached else ""
        print(f"  - {r.stage}{marker} -> {r.artifact_path}")
        for note in r.notes:
            print(f"      ! {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
