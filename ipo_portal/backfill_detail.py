"""Backfill BSE per-issue detail + bid books for historical IPO_NOs.

The per-issue endpoints (``issue_detail``, ``bid_details``,
``consolidated_bid_details``, ``consolidated_bid_details_new``) are only
fetched live for *active* issues, so history is thin. BSE serves them by
``IPO_NO`` for past issues too, and IPO_NOs are roughly sequential — so a
one-time range sweep recovers lot size / BRLM / registrar / subscription
splits for past mainboard + SME IPOs.

Each non-empty response is stored via the standard
``save_raw_snapshot`` envelope under ``data/raw/bse/<endpoint>_<IPO_NO>/``,
exactly where the v2 parsers (``bse_issue_detail``, ``bse_bid_summary``)
expect them — so a subsequent ``normalize`` merges them onto the
canonical records via the ``bse:ipo_no`` alias.

Usage:
    python -m ipo_portal.backfill_detail --start 7000 --end 7800
    python -m ipo_portal.backfill_detail --from-public-issue   # only known IPO_NOs

Polite throttling + skip-if-present make it safe to resume.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Iterable

import requests

from .storage import save_raw_snapshot, utc_now


API = "https://api.bseindia.com/BseIndiaAPI/api"
REFERER = "https://www.bseindia.com/publicissue"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# endpoint_name -> URL template (with {ipo_no})
ENDPOINTS = {
    "issue_detail": f"{API}/GetMkt_ISSUE_BBS_IPO/w?IPO_NO={{ipo_no}}",
    "bid_details": f"{API}/Pubissues_GetBkbldgCatdem_ng/w?IPO_NO={{ipo_no}}",
    "consolidated_bid_details": f"{API}/Pubissues_GetBkbldgCatdem_PAR_ng/w?IPO_NO={{ipo_no}}",
    "consolidated_bid_details_new": f"{API}/Pubissues_GetBkbldgCatdem_PAR_bbnew_ng/w?IPO_NO={{ipo_no}}",
}


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json,*/*", "Referer": REFERER})
    return s


def _is_empty(body: Any) -> bool:
    if body is None:
        return True
    if isinstance(body, dict):
        # All tables empty / no master row.
        meaningful = [v for v in body.values() if isinstance(v, list) and v]
        return not meaningful
    if isinstance(body, list):
        return not body
    return False


def _already_have(root: Path, endpoint: str, ipo_no: int) -> bool:
    d = root / "raw" / "bse" / f"{endpoint}_{ipo_no}"
    return d.exists() and any(d.glob("*.json"))


def known_ipo_nos(root: Path) -> list[int]:
    """IPO_NOs already seen in the BSE public_issue_details snapshots."""
    out: set[int] = set()
    for snap in (root / "raw" / "bse" / "public_issue_details").glob("*.json") if (root / "raw" / "bse" / "public_issue_details").exists() else []:
        try:
            body = json.loads(snap.read_text(encoding="utf-8")).get("body") or {}
        except json.JSONDecodeError:
            continue
        rows = body.get("Table") if isinstance(body, dict) else None
        for r in rows or []:
            n = r.get("IPO_NO")
            if str(n or "").isdigit():
                out.add(int(n))
    return sorted(out)


def backfill(
    ipo_nos: Iterable[int],
    data_root: Path = Path("data"),
    throttle: float = 0.4,
    skip_existing: bool = True,
    only_issue_detail: bool = False,
) -> dict[str, int]:
    """Fetch per-issue endpoints for each IPO_NO; store non-empty responses."""
    session = _session()
    report = {"fetched": 0, "stored": 0, "empty": 0, "errors": 0, "skipped": 0}
    endpoints = {"issue_detail": ENDPOINTS["issue_detail"]} if only_issue_detail else ENDPOINTS
    for ipo_no in ipo_nos:
        for name, tmpl in endpoints.items():
            if skip_existing and _already_have(data_root, name, ipo_no):
                report["skipped"] += 1
                continue
            url = tmpl.format(ipo_no=ipo_no)
            try:
                resp = session.get(url, timeout=30)
                report["fetched"] += 1
                if resp.status_code >= 400:
                    report["errors"] += 1
                    continue
                body = resp.json()
            except (requests.RequestException, json.JSONDecodeError):
                report["errors"] += 1
                time.sleep(throttle)
                continue
            if _is_empty(body):
                report["empty"] += 1
            else:
                save_raw_snapshot(
                    root=data_root, source="bse", endpoint_name=f"{name}_{ipo_no}",
                    url=url, body=body, fetched_at=utc_now(), status_code=resp.status_code,
                )
                report["stored"] += 1
            time.sleep(throttle)
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", type=int, help="First IPO_NO (range mode).")
    p.add_argument("--end", type=int, help="Last IPO_NO inclusive (range mode).")
    p.add_argument("--from-public-issue", action="store_true", help="Only IPO_NOs seen in public_issue_details.")
    p.add_argument("--data-root", type=Path, default=Path("data"))
    p.add_argument("--throttle", type=float, default=0.4)
    p.add_argument("--only-issue-detail", action="store_true", help="Skip bid endpoints (metadata only, faster).")
    args = p.parse_args(argv)

    if args.from_public_issue:
        nos = known_ipo_nos(args.data_root)
    elif args.start and args.end:
        nos = list(range(args.start, args.end + 1))
    else:
        p.error("provide --start/--end or --from-public-issue")
        return 2

    print(f"[backfill-detail] {len(nos)} IPO_NO(s)…", flush=True)
    report = backfill(
        nos, data_root=args.data_root, throttle=args.throttle,
        only_issue_detail=args.only_issue_detail,
    )
    print(f"[backfill-detail] {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
