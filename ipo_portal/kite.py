from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from .performance_export import (
    export_performance_site_data,
    is_probable_debt_public_issue,
    issue_price_for_performance,
    listing_date_for_performance,
)
from .storage import utc_now, write_json


API_ROOT = "https://api.kite.trade"
LOGIN_URL = "https://kite.zerodha.com/connect/login"
DEFAULT_DB_PATH = Path("data/private/kite/kite.sqlite")
DEFAULT_SESSION_PATH = Path("data/private/kite/session.json")
DEFAULT_DATA_DIR = Path("data")
BENCHMARKS = [
    {"key": "nifty500", "label": "Nifty 500", "instrument_token": 268041},
    {"key": "nifty_largemid250", "label": "Nifty LargeMidcap 250", "instrument_token": 289545},
]


class KiteApiError(RuntimeError):
    """Raised for Kite API responses that should be handled by the caller."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Kite-derived IPO listing/current price cache.")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--session-path", default=str(DEFAULT_SESSION_PATH))

    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("login-url", help="Print the Kite login URL (manual flow).")

    exchange = subcommands.add_parser("exchange-token", help="Exchange a request_token for a private access token.")
    exchange.add_argument("--request-token", required=True)

    subcommands.add_parser(
        "auto-login",
        help="Automated TOTP login → access token (needs KITE_USER_ID/PASSWORD/TOTP_SECRET in .env).",
    )
    subcommands.add_parser(
        "ensure-session",
        help="Reuse a fresh token, or TOTP-login if missing/expired. The daily-cron entrypoint.",
    )

    subcommands.add_parser("refresh-instruments", help="Download Kite instrument master into the private DB.")
    map_parser = subcommands.add_parser("map-issues", help="Map IPO issues to Kite instruments.")
    map_parser.add_argument(
        "--site-version",
        choices=["v1", "v2"],
        default="v2",
        help="Which site tree to map from (default: v2 — cleaner SME board_type + consolidated records).",
    )

    backfill = subcommands.add_parser("backfill-listings", help="Fetch listing-day daily candles for mapped issues.")
    backfill.add_argument("--limit", type=int, default=0, help="Optional max number of unmapped listing fetches.")
    backfill.add_argument("--sleep", type=float, default=0.35, help="Delay between historical requests.")

    current = subcommands.add_parser("refresh-current", help="Fetch current LTPs for mapped issues.")
    current.add_argument("--chunk-size", type=int, default=250)

    subcommands.add_parser("refresh-benchmarks", help="Fetch benchmark index daily candles for IPO comparisons.")
    subcommands.add_parser("export-site", help="Export Kite-aware static performance data.")
    subcommands.add_parser("export-v2", help="Compute listing-gain/current performance → data/raw/kite/performance for the v2 normalizer.")
    subcommands.add_parser("sync", help="Run instruments, mapping, listing backfill, current LTP, and site export.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_dotenv(Path(".env"))
    data_dir = Path(args.data_dir)
    db_path = Path(args.db_path)
    session_path = Path(args.session_path)
    init_db(db_path)

    if args.command == "login-url":
        print(login_url(required_env("KITE_API_KEY")))
        return 0
    if args.command == "exchange-token":
        token = exchange_token(args.request_token, session_path)
        print(f"stored Kite access token at {session_path} for user {token.get('user_id') or 'unknown'}")
        return 0
    if args.command == "auto-login":
        from .kite_auth import auto_login

        record = auto_login(session_path)
        print(f"TOTP login ok — access token stored at {session_path} for {record.get('user_id') or 'unknown'}")
        return 0
    if args.command == "ensure-session":
        from .kite_auth import ensure_session

        record = ensure_session(session_path)
        print(f"session ready for {record.get('user_id') or 'unknown'} (method={record.get('login_method', 'manual')})")
        return 0
    if args.command == "refresh-instruments":
        count = refresh_instruments(db_path, session_path)
        print(f"stored {count} Kite instruments")
        return 0
    if args.command == "map-issues":
        count = map_issues(db_path, data_dir, site_version=getattr(args, "site_version", "v2"))
        print(f"mapped {count} IPO issues to Kite instruments (from {getattr(args, 'site_version', 'v2')})")
        return 0
    if args.command == "backfill-listings":
        report = backfill_listings(db_path, session_path, limit=args.limit, sleep_seconds=args.sleep)
        print(f"listing candles: {report['ok']} ok, {report['missing']} missing, {report['errors']} errors")
        return 0
    if args.command == "refresh-current":
        report = refresh_current_prices(db_path, session_path, chunk_size=args.chunk_size)
        print(f"current prices: {report['ok']} ok, {report['missing']} missing")
        return 0
    if args.command == "refresh-benchmarks":
        report = refresh_benchmarks(db_path, session_path)
        print(f"benchmark candles: {report['ok']} ok, {report['errors']} errors")
        return 0
    if args.command == "export-site":
        summary = export_site(data_dir)
        print(f"exported {summary['total_rows']} performance rows in {summary['page_count']} pages")
        return 0
    if args.command == "export-v2":
        from .kite_v2 import export_snapshot

        path = export_snapshot(db_path=db_path, data_root=data_dir)
        print(f"wrote Kite v2 performance snapshot → {path}")
        return 0
    if args.command == "sync":
        # Auto-refresh the daily token first if TOTP creds are configured;
        # otherwise fall through to the existing (manual) session.
        try:
            from .kite_auth import ensure_session

            ensure_session(session_path)
        except Exception as exc:  # noqa: BLE001 — manual session may already be valid
            print(f"[kite] auto-login skipped ({exc}); using existing session if present.")
        instrument_count = refresh_instruments(db_path, session_path)
        mapped_count = map_issues(db_path, data_dir)
        listing_report = backfill_listings(db_path, session_path)
        current_report = refresh_current_prices(db_path, session_path)
        refresh_benchmarks(db_path, session_path)
        summary = export_site(data_dir)
        # Also emit the v2 performance snapshot for the v2 normalizer.
        from .kite_v2 import export_snapshot

        export_snapshot(db_path=db_path, data_root=data_dir)
        print(
            "sync complete: "
            f"{instrument_count} instruments, {mapped_count} mapped, "
            f"{listing_report['ok']} listing candles, {current_report['ok']} current prices, "
            f"{summary['total_rows']} exported rows"
        )
        return 0
    raise SystemExit(f"Unsupported command: {args.command}")


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing {name}. Put it in .env or pass it in the process environment.")
    return value


def login_url(api_key: str) -> str:
    return f"{LOGIN_URL}?{urlencode({'v': '3', 'api_key': api_key})}"


def exchange_token(request_token: str, session_path: Path) -> dict[str, Any]:
    api_key = required_env("KITE_API_KEY")
    api_secret = required_env("KITE_API_SECRET")
    checksum = hashlib.sha256(f"{api_key}{request_token}{api_secret}".encode("utf-8")).hexdigest()
    response = requests.post(
        f"{API_ROOT}/session/token",
        data={"api_key": api_key, "request_token": request_token, "checksum": checksum},
        headers={"X-Kite-Version": "3"},
        timeout=30,
    )
    payload = parse_response(response)
    data = payload.get("data") or {}
    if not data.get("access_token"):
        raise SystemExit("Kite token exchange succeeded without an access_token")
    session_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(
        session_path,
        {
            "api_key": api_key,
            "access_token": data["access_token"],
            "user_id": data.get("user_id"),
            "user_name": data.get("user_name"),
            "login_time": data.get("login_time"),
            "fetched_at": utc_now().isoformat(),
        },
    )
    return data


def read_session(session_path: Path) -> dict[str, str]:
    if not session_path.exists():
        raise SystemExit(f"Missing Kite session at {session_path}; run exchange-token first.")
    session = json.loads(session_path.read_text(encoding="utf-8"))
    api_key = session.get("api_key") or os.environ.get("KITE_API_KEY")
    access_token = session.get("access_token")
    if not api_key or not access_token:
        raise SystemExit(f"Incomplete Kite session at {session_path}")
    return {"api_key": api_key, "access_token": access_token}


def kite_headers(session: dict[str, str]) -> dict[str, str]:
    return {
        "X-Kite-Version": "3",
        "Authorization": f"token {session['api_key']}:{session['access_token']}",
    }


def parse_response(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise KiteApiError(f"Kite returned non-JSON response ({response.status_code})") from exc
    if response.status_code >= 400 or payload.get("status") == "error":
        message = payload.get("message") or response.text[:200]
        raise KiteApiError(f"Kite API error ({response.status_code}): {message}")
    return payload


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            create table if not exists kite_instruments (
              instrument_token integer primary key,
              exchange_token text,
              tradingsymbol text not null,
              name text,
              last_price real,
              expiry text,
              strike real,
              tick_size real,
              lot_size integer,
              instrument_type text,
              segment text,
              exchange text,
              fetched_at text not null
            );
            create index if not exists idx_kite_instruments_symbol on kite_instruments(exchange, tradingsymbol);
            create index if not exists idx_kite_instruments_name on kite_instruments(name);

            create table if not exists ipo_symbol_map (
              issue_slug text primary key,
              company_name text not null,
              listing_date text,
              issue_price real,
              exchange_platform text,
              source_symbol text,
              kite_exchange text,
              tradingsymbol text,
              instrument_token integer,
              confidence real not null,
              match_reason text,
              reviewed integer not null default 0,
              updated_at text not null
            );

            create table if not exists kite_listing_prices (
              issue_slug text primary key,
              instrument_token integer,
              requested_listing_date text,
              candle_date text,
              open real,
              high real,
              low real,
              close real,
              volume integer,
              status text not null,
              error text,
              fetched_at text not null
            );

            create table if not exists kite_current_prices (
              issue_slug text primary key,
              instrument_token integer,
              kite_exchange text,
              tradingsymbol text,
              last_price real,
              fetched_at text not null,
              status text not null,
              error text
            );

            create table if not exists kite_benchmark_prices (
              benchmark_key text not null,
              benchmark_label text not null,
              instrument_token integer not null,
              candle_date text not null,
              close real not null,
              fetched_at text not null,
              primary key (benchmark_key, candle_date)
            );
            create index if not exists idx_kite_benchmark_prices_date on kite_benchmark_prices(benchmark_key, candle_date);
            """
        )
        conn.commit()
    finally:
        conn.close()


def refresh_instruments(db_path: Path, session_path: Path) -> int:
    session = read_session(session_path)
    response = requests.get(f"{API_ROOT}/instruments", headers=kite_headers(session), timeout=60)
    if response.status_code >= 400:
        parse_response(response)
    fetched_at = utc_now().isoformat()
    rows = list(csv.DictReader(response.text.splitlines()))
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("delete from kite_instruments")
        conn.executemany(
            """
            insert into kite_instruments (
              instrument_token, exchange_token, tradingsymbol, name, last_price, expiry, strike,
              tick_size, lot_size, instrument_type, segment, exchange, fetched_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    int(row["instrument_token"]),
                    row.get("exchange_token") or None,
                    row.get("tradingsymbol") or "",
                    row.get("name") or None,
                    to_float(row.get("last_price")),
                    row.get("expiry") or None,
                    to_float(row.get("strike")),
                    to_float(row.get("tick_size")),
                    to_int(row.get("lot_size")),
                    row.get("instrument_type") or None,
                    row.get("segment") or None,
                    row.get("exchange") or None,
                    fetched_at,
                )
                for row in rows
                if row.get("instrument_token") and row.get("tradingsymbol")
            ],
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def map_issues(db_path: Path, data_dir: Path, site_version: str = "v2") -> int:
    if site_version == "v2":
        issues = load_performance_issues_v2(data_dir)
    else:
        issues = load_performance_issues(data_dir)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        instruments = [
            dict(row)
            for row in conn.execute(
                """
                select instrument_token, exchange, tradingsymbol, name, segment, instrument_type
                from kite_instruments
                where exchange in ('NSE', 'BSE') and instrument_type = 'EQ'
                """
            )
        ]
        by_symbol: dict[tuple[str, str], dict[str, Any]] = {}
        by_name: dict[str, list[dict[str, Any]]] = {}
        for inst in instruments:
            by_symbol[(inst["exchange"], inst["tradingsymbol"].upper())] = inst
            by_name.setdefault(normalize_name(inst.get("name")), []).append(inst)

        now = utc_now().isoformat()
        mapped = 0
        conn.execute("create temporary table if not exists valid_ipo_issue_slugs (issue_slug text primary key)")
        conn.execute("delete from valid_ipo_issue_slugs")
        conn.executemany("insert into valid_ipo_issue_slugs (issue_slug) values (?)", [(issue["slug"],) for issue in issues])
        conn.execute("delete from ipo_symbol_map where issue_slug not in (select issue_slug from valid_ipo_issue_slugs)")
        # NOTE: we deliberately do NOT prune kite_listing_prices /
        # kite_current_prices here. Those are instrument-level facts
        # (a listing candle is the same regardless of which slug scheme
        # references it). Pruning on a v1→v2 slug cutover would discard the
        # expensive backfilled candles; instead the v2 performance export
        # joins them back by instrument_token.
        for issue in issues:
            match = choose_instrument(issue, by_symbol, by_name)
            conn.execute(
                """
                insert into ipo_symbol_map (
                  issue_slug, company_name, listing_date, issue_price, exchange_platform, source_symbol,
                  kite_exchange, tradingsymbol, instrument_token, confidence, match_reason, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(issue_slug) do update set
                  company_name=excluded.company_name,
                  listing_date=excluded.listing_date,
                  issue_price=excluded.issue_price,
                  exchange_platform=excluded.exchange_platform,
                  source_symbol=excluded.source_symbol,
                  kite_exchange=excluded.kite_exchange,
                  tradingsymbol=excluded.tradingsymbol,
                  instrument_token=excluded.instrument_token,
                  confidence=excluded.confidence,
                  match_reason=excluded.match_reason,
                  updated_at=excluded.updated_at
                """,
                (
                    issue["slug"],
                    issue["company_name"],
                    issue.get("listing_date"),
                    issue.get("issue_price"),
                    issue.get("exchange_platform"),
                    issue.get("symbol"),
                    match.get("exchange") if match else None,
                    match.get("tradingsymbol") if match else None,
                    match.get("instrument_token") if match else None,
                    match.get("confidence", 0) if match else 0,
                    match.get("reason") if match else "no_match",
                    now,
                ),
            )
            if match:
                mapped += 1
        conn.commit()
        return mapped
    finally:
        conn.close()


def load_performance_issues_v2(data_dir: Path) -> list[dict[str, Any]]:
    """Load issues to map from the v2 tree (``data/site_v2/issues/by-slug/``).

    v2 gives us a clean ``board_type`` (SME vs Main Board), one consolidated
    record per SME, and exchange-qualified aliases — so we can pin each SME
    to the single exchange it actually lists on (NSE Emerge *or* BSE SME)
    and avoid cross-exchange false matches.

    Returns the same row shape as ``load_performance_issues`` plus
    ``is_sme`` and a precise ``exchange_platform`` ("NSE" / "BSE" /
    "NSE BSE").
    """
    by_slug = data_dir / "site_v2" / "issues" / "by-slug"
    if not by_slug.exists():
        raise SystemExit(f"Missing {by_slug}; run the v2 normalize first.")
    issues: list[dict[str, Any]] = []
    for path in sorted(by_slug.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        identity = doc.get("identity") or {}
        # Only equity IPO-family issues are price-mappable to Kite EQ.
        if (identity.get("issue_type") or "") not in ("IPO", "FPO"):
            continue
        pricing = doc.get("pricing") or {}
        issue_price_paise = pricing.get("issue_price_paise")
        issue_price = (issue_price_paise / 100) if isinstance(issue_price_paise, (int, float)) else None
        is_sme = identity.get("board_type") == "SME Board"
        issues.append(
            {
                "slug": identity.get("slug") or doc.get("slug"),
                "company_name": identity.get("company_name"),
                "listing_date": (doc.get("timeline") or {}).get("listing_date"),
                "issue_price": issue_price,
                "symbol": identity.get("symbol"),
                "is_sme": is_sme,
                "exchange_platform": _v2_exchange_platform(identity),
            }
        )
    return issues


def _v2_exchange_platform(identity: dict[str, Any]) -> str:
    """Derive a precise exchange hint from v2 identity signals.

    Signals: the NSE-style ``symbol``, BSE aliases (``bse:scrip_code:…``,
    ``bse:stock_page:…``), and Kite venue aliases (``kite:nse:…`` /
    ``kite:bse:…``). For a single-exchange SME this resolves to exactly
    one exchange, which the matcher then enforces.
    """
    aliases = identity.get("aliases") or []
    has_bse = any(isinstance(a, str) and (a.startswith("bse:") or a.startswith("kite:bse:")) for a in aliases)
    has_nse_alias = any(isinstance(a, str) and a.startswith("kite:nse:") for a in aliases)
    has_nse_symbol = bool(identity.get("symbol"))
    has_nse = has_nse_symbol or has_nse_alias
    if has_nse and not has_bse:
        return "NSE"
    if has_bse and not has_nse:
        return "BSE"
    return "NSE BSE"


def load_performance_issues(data_dir: Path) -> list[dict[str, Any]]:
    performance_path = data_dir / "site" / "issues" / "performance.json"
    if not performance_path.exists():
        raise SystemExit(f"Missing {performance_path}; run the site data build first.")
    rows = json.loads(performance_path.read_text(encoding="utf-8"))
    issues = []
    for row in rows:
        if normalize_issue_type(row.get("issue_type")) != "ipo":
            continue
        detail_path = data_dir / "site" / "issues" / "by-slug" / f"{row['slug']}.json"
        symbol = None
        issue_price = row.get("issue_price")
        listing_date = row.get("listing_date")
        if detail_path.exists():
            try:
                detail = json.loads(detail_path.read_text(encoding="utf-8"))
                if is_probable_debt_public_issue(detail):
                    continue
                symbol = (detail.get("company") or {}).get("symbol")
                issue_price = issue_price_for_performance(detail.get("pricing") or {})
                listing_date = listing_date_for_performance(detail)
            except json.JSONDecodeError:
                symbol = None
        issues.append({**row, "symbol": symbol, "issue_price": issue_price, "listing_date": listing_date})
    return issues


def choose_instrument(issue: dict[str, Any], by_symbol: dict[tuple[str, str], dict[str, Any]], by_name: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    exchanges = preferred_exchanges(issue.get("exchange_platform"))

    # SME-strict (v2): an SME issue lists on only ONE exchange (NSE Emerge
    # or BSE SME). When v2 resolved that exchange precisely, restrict
    # matching to it — a name-only fallback to the other exchange would be
    # a different company. Only applies when the platform is unambiguous.
    platform = (issue.get("exchange_platform") or "").upper()
    sme_strict = bool(issue.get("is_sme")) and platform in ("NSE", "BSE")
    if sme_strict:
        exchanges = [platform]

    symbol = clean_symbol(issue.get("symbol"))
    if symbol:
        for exchange in exchanges:
            inst = by_symbol.get((exchange, symbol))
            if inst:
                return {**inst, "confidence": 1.0, "reason": "exact_symbol"}

    company_name = normalize_name(issue.get("company_name"))
    candidates = by_name.get(company_name, [])
    if sme_strict:
        candidates = [c for c in candidates if c["exchange"] in exchanges]
    if candidates:
        ranked = sorted(candidates, key=lambda item: exchanges.index(item["exchange"]) if item["exchange"] in exchanges else 99)
        inst = ranked[0]
        return {**inst, "confidence": 0.92, "reason": "exact_name_sme" if sme_strict else "exact_name"}

    best: tuple[float, dict[str, Any]] | None = None
    company_tokens = set(company_name.split())
    if len(company_tokens) >= 2:
        for name, candidates_for_name in by_name.items():
            tokens = set(name.split())
            overlap = len(company_tokens & tokens)
            score = overlap / max(len(company_tokens | tokens), 1)
            if score < 0.74:
                continue
            pool = [c for c in candidates_for_name if c["exchange"] in exchanges] if sme_strict else candidates_for_name
            if not pool:
                continue
            inst = sorted(pool, key=lambda item: exchanges.index(item["exchange"]) if item["exchange"] in exchanges else 99)[0]
            if best is None or score > best[0]:
                best = (score, inst)
    if best:
        return {**best[1], "confidence": round(best[0], 2), "reason": "fuzzy_name"}
    return None


def backfill_listings(db_path: Path, session_path: Path, limit: int = 0, sleep_seconds: float = 0.35) -> dict[str, int]:
    session = read_session(session_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    report = {"ok": 0, "missing": 0, "errors": 0}
    try:
        rows = conn.execute(
            """
            select m.issue_slug, m.company_name, m.exchange_platform, m.instrument_token,
                   m.kite_exchange, m.tradingsymbol, m.listing_date
            from ipo_symbol_map m
            left join kite_listing_prices p on p.issue_slug = m.issue_slug
            where m.instrument_token is not null
              and m.listing_date is not null
              and (p.issue_slug is null or p.status in ('error', 'missing'))
            order by m.listing_date desc
            """
        ).fetchall()
        if limit:
            rows = rows[:limit]
        for row in rows:
            status = "missing"
            error = None
            candle = None
            instrument_token = int(row["instrument_token"])
            try:
                candle = fetch_listing_candle(session, instrument_token, date.fromisoformat(row["listing_date"]))
            except KiteApiError as exc:
                try:
                    fallback = fetch_alternate_listing_candle(conn, session, row)
                    if fallback is None:
                        raise exc
                    candle = fallback["candle"]
                    instrument_token = int(fallback["instrument_token"])
                    conn.execute(
                        """
                        update ipo_symbol_map
                        set kite_exchange = ?, tradingsymbol = ?, instrument_token = ?, match_reason = ?, updated_at = ?
                        where issue_slug = ?
                        """,
                        (
                            fallback["exchange"],
                            fallback["tradingsymbol"],
                            fallback["instrument_token"],
                            "listing_history_fallback",
                            utc_now().isoformat(),
                            row["issue_slug"],
                        ),
                    )
                except Exception as fallback_exc:  # noqa: BLE001 - preserve both primary and fallback failures.
                    status = "error"
                    error = str(fallback_exc)[:300]
            except Exception as exc:  # noqa: BLE001 - preserve error in local audit cache.
                status = "error"
                error = str(exc)[:300]
            if error is None:
                status = "ok" if candle and candle["date"] == row["listing_date"] else "shifted" if candle else "missing"
            conn.execute(
                """
                insert into kite_listing_prices (
                  issue_slug, instrument_token, requested_listing_date, candle_date, open, high, low, close, volume,
                  status, error, fetched_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(issue_slug) do update set
                  instrument_token=excluded.instrument_token,
                  requested_listing_date=excluded.requested_listing_date,
                  candle_date=excluded.candle_date,
                  open=excluded.open,
                  high=excluded.high,
                  low=excluded.low,
                  close=excluded.close,
                  volume=excluded.volume,
                  status=excluded.status,
                  error=excluded.error,
                  fetched_at=excluded.fetched_at
                """,
                (
                    row["issue_slug"],
                    instrument_token,
                    row["listing_date"],
                    candle.get("date") if candle else None,
                    candle.get("open") if candle else None,
                    candle.get("high") if candle else None,
                    candle.get("low") if candle else None,
                    candle.get("close") if candle else None,
                    candle.get("volume") if candle else None,
                    status,
                    error,
                    utc_now().isoformat(),
                ),
            )
            report["ok" if status in {"ok", "shifted"} else "missing" if status == "missing" else "errors"] += 1
            conn.commit()
            time.sleep(max(0, sleep_seconds))
        return report
    finally:
        conn.close()


def fetch_listing_candle(session: dict[str, str], instrument_token: int, listing_date: date) -> dict[str, Any] | None:
    from_date = listing_date.isoformat()
    to_date = (listing_date + timedelta(days=10)).isoformat()
    payload = None
    last_error: KiteApiError | None = None
    for attempt in range(3):
        response = requests.get(
            f"{API_ROOT}/instruments/historical/{instrument_token}/day",
            params={"from": f"{from_date} 00:00:00", "to": f"{to_date} 23:59:59"},
            headers=kite_headers(session),
            timeout=30,
        )
        try:
            payload = parse_response(response)
            break
        except KiteApiError as exc:
            last_error = exc
            if attempt == 2:
                raise
            time.sleep(1.5 * (attempt + 1))
    if payload is None:
        raise last_error or KiteApiError("Kite historical request failed without a response payload")
    candles = ((payload.get("data") or {}).get("candles")) or []
    if not candles:
        return None
    first = candles[0]
    candle_date = str(first[0])[:10]
    return {"date": candle_date, "open": first[1], "high": first[2], "low": first[3], "close": first[4], "volume": first[5] if len(first) > 5 else None}


def fetch_alternate_listing_candle(conn: sqlite3.Connection, session: dict[str, str], row: sqlite3.Row) -> dict[str, Any] | None:
    company_name = normalize_name(row["company_name"])
    if not company_name:
        return None
    allowed_exchanges = listing_fallback_exchanges(row)
    candidates = [
        dict(candidate)
        for candidate in conn.execute(
            """
            select instrument_token, exchange, tradingsymbol, name
            from kite_instruments
            where exchange in ('BSE', 'NSE')
              and instrument_type = 'EQ'
              and instrument_token != ?
              and name is not null
            """,
            (row["instrument_token"],),
        )
        if candidate["exchange"] in allowed_exchanges and normalize_name(candidate["name"]) == company_name
    ]
    candidates.sort(key=lambda item: 0 if item["exchange"] == "BSE" else 1)
    for candidate in candidates:
        try:
            candle = fetch_listing_candle(session, int(candidate["instrument_token"]), date.fromisoformat(row["listing_date"]))
        except KiteApiError:
            continue
        return {
            "candle": candle,
            "instrument_token": candidate["instrument_token"],
            "exchange": candidate["exchange"],
            "tradingsymbol": candidate["tradingsymbol"],
        }
    return None


def listing_fallback_exchanges(row: sqlite3.Row) -> set[str]:
    primary_exchange = row["kite_exchange"]
    platform = (row["exchange_platform"] or "").upper()
    symbol = (row["tradingsymbol"] or "").upper()
    if symbol.endswith(("-SM", "-ST", "-BZ", "-SZ")):
        return {primary_exchange} if primary_exchange else set()
    if "BSE" in platform and "NSE" in platform:
        return {"BSE", "NSE"}
    if "BSE" in platform:
        return {"BSE"}
    if "NSE" in platform:
        return {"NSE"}
    return {"BSE", "NSE"} if primary_exchange not in {"BSE", "NSE"} else {primary_exchange, "BSE", "NSE"}


def refresh_current_prices(db_path: Path, session_path: Path, chunk_size: int = 250) -> dict[str, int]:
    session = read_session(session_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    report = {"ok": 0, "missing": 0}
    try:
        rows = conn.execute(
            """
            select issue_slug, kite_exchange, tradingsymbol, instrument_token
            from ipo_symbol_map
            where instrument_token is not null and kite_exchange is not null and tradingsymbol is not null
            order by listing_date desc
            """
        ).fetchall()
        now = utc_now().isoformat()
        for chunk in chunks(rows, max(1, chunk_size)):
            instruments = [f"{row['kite_exchange']}:{row['tradingsymbol']}" for row in chunk]
            response = requests.get(f"{API_ROOT}/quote/ltp", params=[("i", item) for item in instruments], headers=kite_headers(session), timeout=30)
            payload = parse_response(response)
            data = payload.get("data") or {}
            for row in chunk:
                key = f"{row['kite_exchange']}:{row['tradingsymbol']}"
                quote = data.get(key)
                last_price = quote.get("last_price") if quote else None
                status = "ok" if last_price is not None else "missing"
                conn.execute(
                    """
                    insert into kite_current_prices (
                      issue_slug, instrument_token, kite_exchange, tradingsymbol, last_price, fetched_at, status, error
                    ) values (?, ?, ?, ?, ?, ?, ?, ?)
                    on conflict(issue_slug) do update set
                      instrument_token=excluded.instrument_token,
                      kite_exchange=excluded.kite_exchange,
                      tradingsymbol=excluded.tradingsymbol,
                      last_price=excluded.last_price,
                      fetched_at=excluded.fetched_at,
                      status=excluded.status,
                      error=excluded.error
                    """,
                    (row["issue_slug"], row["instrument_token"], row["kite_exchange"], row["tradingsymbol"], last_price, now, status, None if quote else "quote_absent"),
                )
                report["ok" if quote else "missing"] += 1
            conn.commit()
            time.sleep(0.35)
        return report
    finally:
        conn.close()


def refresh_benchmarks(db_path: Path, session_path: Path) -> dict[str, int]:
    session = read_session(session_path)
    conn = sqlite3.connect(db_path)
    report = {"ok": 0, "errors": 0}
    try:
        fetched_at = utc_now().isoformat()
        today = date.today()
        for benchmark in BENCHMARKS:
            try:
                candles = []
                start = date(2001, 1, 1)
                while start <= today:
                    end = min(start + timedelta(days=1800), today)
                    response = requests.get(
                        f"{API_ROOT}/instruments/historical/{benchmark['instrument_token']}/day",
                        params={"from": f"{start.isoformat()} 00:00:00", "to": f"{end.isoformat()} 23:59:59"},
                        headers=kite_headers(session),
                        timeout=60,
                    )
                    payload = parse_response(response)
                    candles.extend(((payload.get("data") or {}).get("candles")) or [])
                    start = end + timedelta(days=1)
                    time.sleep(0.25)
                conn.execute("delete from kite_benchmark_prices where benchmark_key = ?", (benchmark["key"],))
                conn.executemany(
                    """
                    insert into kite_benchmark_prices (
                      benchmark_key, benchmark_label, instrument_token, candle_date, close, fetched_at
                    ) values (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            benchmark["key"],
                            benchmark["label"],
                            benchmark["instrument_token"],
                            str(candle[0])[:10],
                            candle[4],
                            fetched_at,
                        )
                        for candle in candles
                        if len(candle) >= 5 and candle[4] is not None
                    ],
                )
                conn.commit()
                report["ok"] += len(candles)
            except Exception:  # noqa: BLE001 - continue exporting other benchmarks.
                report["errors"] += 1
                conn.rollback()
            time.sleep(0.35)
        return report
    finally:
        conn.close()


def export_site(data_dir: Path) -> dict[str, Any]:
    issues_path = data_dir / "site" / "issues" / "index.json"
    if not issues_path.exists():
        raise SystemExit(f"Missing {issues_path}; run the site build first.")
    issues = json.loads(issues_path.read_text(encoding="utf-8"))
    return export_performance_site_data(data_dir / "site", issues)


def preferred_exchanges(exchange_platform: str | None) -> list[str]:
    text = (exchange_platform or "").lower()
    if "bse" in text and "nse" not in text:
        return ["BSE", "NSE"]
    return ["NSE", "BSE"]


def clean_symbol(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"[^A-Za-z0-9-]", "", value).upper()
    return cleaned or None


def normalize_issue_type(value: str | None) -> str:
    text = (value or "").lower().replace(" ", "").replace("_", "").replace("-", "")
    return "ipo" if text == "ipo" else text


def normalize_name(value: str | None) -> str:
    text = (value or "").lower()
    text = re.sub(r"&", " and ", text)
    text = re.sub(r"\b(limited|ltd|private|pvt|the|india|indian)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def to_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def to_int(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def chunks(values: list[Any], size: int) -> list[list[Any]]:
    return [values[i : i + size] for i in range(0, len(values), size)]


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KiteApiError as exc:
        raise SystemExit(str(exc)) from None
