"""Systematic data-quality audit of a site dataset — hunt for regression classes.

Scans every issue record and runs a battery of sanity checks across
units, dates, status, pricing, subscription, listing performance,
identity, enums, duplicates, and thin records. Prints a report: per
check, a count and a few sample slugs. Read-only.

Run: python scripts/audit_v2_quality.py --site-root data/ipo_watch_v3
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SITE_ROOT = ROOT / "data" / "ipo_watch_v3"
if not DEFAULT_SITE_ROOT.exists():
    DEFAULT_SITE_ROOT = ROOT / "data" / "site_v2"
TODAY = date(2026, 5, 24)

ISSUE_TYPES = {"IPO", "FPO", "Rights", "Buyback", "OFS", "NCD", "SGB", "InvIT", "REIT", "Others"}
STATUSES = {"Filed", "Open", "Closed", "Listed", "Withdrawn", "Upcoming"}
BOARDS = {"Main Board", "SME Board"}
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)+$")
ISIN_RE = re.compile(r"^IN[0-9A-Z]{10}$")

findings: dict[str, list[str]] = defaultdict(list)


def flag(check: str, slug: str) -> None:
    findings[check].append(slug)


def _d(v):
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def audit(doc: dict) -> None:
    slug = doc.get("slug") or "?"
    idn = doc.get("identity") or {}
    pricing = doc.get("pricing") or {}
    tl = doc.get("timeline") or {}
    sub = doc.get("subscription") or {}
    perf = doc.get("listing_performance") or {}
    source_names = {s.get("source") for s in doc.get("sources") or [] if isinstance(s, dict)}
    for path, prov in (doc.get("field_provenance") or {}).items():
        if not isinstance(prov, dict):
            continue
        src = prov.get("source")
        if src and src not in source_names and src not in {"inferred", "rhp"}:
            flag("provenance.source_missing_from_sources", slug)
            break

    # --- enums ---
    if idn.get("issue_type") and idn["issue_type"] not in ISSUE_TYPES:
        flag("enum.issue_type", slug)
    if idn.get("status") and idn["status"] not in STATUSES:
        flag("enum.status", slug)
    if idn.get("board_type") and idn["board_type"] not in BOARDS:
        flag("enum.board_type", slug)

    # --- identity ---
    if not idn.get("company_name"):
        flag("identity.no_company_name", slug)
    if idn.get("slug") and not SLUG_RE.match(idn["slug"]):
        flag("identity.bad_slug", slug)
    if idn.get("isin") and not ISIN_RE.match(idn["isin"]):
        flag("identity.bad_isin", slug)

    # --- pricing units / sanity ---
    ip = pricing.get("issue_price_paise")
    lo = pricing.get("price_band_lower_paise")
    hi = pricing.get("price_band_upper_paise")
    fv = pricing.get("face_value_paise")
    is_reit_invit = idn.get("issue_type") in ("REIT", "InvIT")  # units legitimately cost lakhs
    for fld, val in (("issue_price", ip), ("band_lower", lo), ("band_upper", hi)):
        if isinstance(val, (int, float)):
            if val <= 0:
                flag(f"pricing.{fld}_nonpositive", slug)
            elif val < 100:           # < ₹1/share — almost certainly a unit error
                flag(f"pricing.{fld}_under_1rupee", slug)
            elif val > 50_00_00_000 and not is_reit_invit:  # > ₹50 lakh/share — implausible
                flag(f"pricing.{fld}_over_50lakh", slug)
    # OFS/Buyback "bands" are a floor/cap or tender range, not a true
    # lower→upper IPO band, so an inverted pair is expected there.
    band_semantics_differ = idn.get("issue_type") in ("OFS", "Buyback")
    if (
        not band_semantics_differ
        and isinstance(lo, (int, float))
        and isinstance(hi, (int, float))
        and lo > hi
    ):
        flag("pricing.band_inverted", slug)
    if not band_semantics_differ and all(isinstance(x, (int, float)) for x in (ip, lo, hi)) and lo and hi:
        if not (lo * 0.99 <= ip <= hi * 1.01):
            flag("pricing.issue_price_outside_band", slug)
    if isinstance(fv, (int, float)) and fv not in (100, 200, 500, 1000, 5000, 10000):
        flag("pricing.unusual_face_value", slug)
    isz = pricing.get("issue_size_paise")
    if isinstance(isz, (int, float)) and isz > 50_000 * 1_00_00_000 * 100:  # > ₹50,000 cr
        flag("pricing.issue_size_over_50kcr", slug)

    # --- dates ---
    od, cd, ld = _d(tl.get("open_date")), _d(tl.get("close_date")), _d(tl.get("listing_date"))
    if od and cd and od > cd:
        flag("date.open_after_close", slug)
    if cd and ld and cd > ld:
        flag("date.close_after_listing", slug)
    if od and ld and od > ld:
        flag("date.open_after_listing", slug)
    for nm, dt in (("open", od), ("close", cd), ("listing", ld)):
        if dt and (dt.year < 1990 or dt.year > TODAY.year + 2):
            flag(f"date.{nm}_implausible_year", slug)

    # --- status consistency ---
    st = idn.get("status")
    # OFS/Buyback happen on an already-listed company — there is no new
    # listing event, so "Listed without a listing date" is expected.
    already_listed_type = idn.get("issue_type") in ("OFS", "Buyback", "FPO", "Rights")
    if st == "Listed" and not ld and not already_listed_type:
        flag("status.listed_no_listing_date", slug)
    if st == "Upcoming" and od and od < TODAY:
        flag("status.upcoming_but_opened", slug)
    if st == "Open" and cd and cd < TODAY:
        flag("status.open_but_closed", slug)
    if st == "Closed" and ld and ld < TODAY:
        flag("status.closed_but_listed", slug)

    # --- subscription ---
    ot = _num(sub.get("overall_times_x"))
    if ot is not None:
        if ot < 0:
            flag("subscription.negative_times", slug)
        elif ot > 1000:
            flag("subscription.times_over_1000x", slug)

    # --- listing performance ---
    lg = perf.get("listing_gain_bps")
    cg = perf.get("current_gain_bps")
    cp = perf.get("current_price_paise")
    # Losing more than 100% is impossible; big positive gains are real
    # (multibaggers up 10-100x over the years), so only the floor is a bug.
    if isinstance(lg, (int, float)) and lg < -10000:
        flag("perf.listing_gain_below_-100pct", slug)
    if isinstance(cg, (int, float)) and cg < -10000:
        flag("perf.current_gain_below_-100pct", slug)
    if isinstance(cp, (int, float)) and cp <= 0:
        flag("perf.current_price_nonpositive", slug)
    ip = pricing.get("issue_price_paise")
    lc = perf.get("listing_close_price_paise")
    if isinstance(ip, int) and ip > 0 and isinstance(lc, int) and isinstance(lg, int):
        expected = round((lc - ip) * 10000 / ip)
        if expected != lg:
            flag("perf.listing_gain_price_mismatch", slug)
    if isinstance(ip, int) and ip > 0 and isinstance(cp, int) and isinstance(cg, int):
        expected = round((cp - ip) * 10000 / ip)
        if expected != cg:
            flag("perf.current_gain_price_mismatch", slug)

    # --- thin records (only a name, no usable data of any kind) ---
    has_book = bool(
        (sub.get("consolidated") or {}).get("categories")
        or (sub.get("by_exchange") or {})
    )
    has_any = any([
        ip, lo, hi, tl.get("open_date"), tl.get("listing_date"),
        sub.get("overall_times_x"), (doc.get("documents") or {}),
        (doc.get("parties") or {}).get("lead_managers"),
        perf.get("listing_gain_bps"), pricing.get("market_lot"),
        (doc.get("book_building") or {}), has_book,
    ])
    if not has_any:
        flag("thin.name_only", slug)


# Integrity-critical classes: their presence means the build is structurally
# broken (silent record loss, orphans, corrupt enums/identity) and must NOT be
# committed. The long-tail data quirks (pricing bands, dates, status, dups) are
# real source characteristics — informational, never a gate failure.
GATE_PREFIXES = ("manifest", "file", "enum")
GATE_CHECKS = (
    "date.close_after_listing",
    "date.open_after_close",
    "date.open_after_listing",
    "identity.bad_slug",
    "identity.bad_isin",
    "identity.no_company_name",
    "perf.current_gain_below_-100pct",
    "perf.current_gain_price_mismatch",
    "perf.current_price_nonpositive",
    "perf.listing_gain_below_-100pct",
    "perf.listing_gain_price_mismatch",
    "pricing.band_inverted",
    "pricing.band_lower_nonpositive",
    "pricing.band_lower_under_1rupee",
    "pricing.band_upper_nonpositive",
    "pricing.band_upper_under_1rupee",
    "pricing.issue_price_nonpositive",
    "pricing.issue_price_outside_band",
    "pricing.issue_price_under_1rupee",
    "provenance.source_missing_from_sources",
    "status.open_but_closed",
)


def _is_gate_finding(check: str) -> bool:
    return check.split(".")[0] in GATE_PREFIXES or check in GATE_CHECKS


def main(gate: bool = False, site_root: Path | None = None) -> int:
    global findings

    findings = defaultdict(list)
    site_root = site_root or DEFAULT_SITE_ROOT
    by_slug = site_root / "issues" / "by-slug"
    if not by_slug.exists():
        flag("file.by_slug_missing", str(by_slug))
        print(f"=== quality audit — 0 issue records ({site_root}) ===\n")
        print(f"🔴 file.by_slug_missing: 1\n      {by_slug}")
        return 1 if gate else 0

    files = sorted(by_slug.glob("*.json"))
    total = 0
    # duplicate detection: (normalized_name, issue_type) -> slugs
    nt: dict[tuple, list[str]] = defaultdict(list)
    for p in files:
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            flag("file.unparseable", p.stem)
            continue
        total += 1
        audit(doc)
        idn = doc.get("identity") or {}
        nm = re.sub(r"[^a-z0-9]+", " ", (idn.get("company_name") or "").lower()).strip()
        nt[(nm, idn.get("issue_type"))].append(doc.get("slug") or p.stem)

    # same (name,type) with >1 record AND overlapping era = likely dup
    for (nm, it), slugs in nt.items():
        if nm and len(slugs) > 1:
            findings["dup.same_name_type"].extend(slugs[:0])  # count via group below
            findings.setdefault("_dup_groups", []).append(f"{nm}|{it}|{len(slugs)}")

    # Manifest/disk parity — catches silent slug collisions (overwrites) and
    # stale orphan files (renames/re-consolidation without pruning).
    manifest_path = site_root / "manifest.json"
    if manifest_path.exists():
        man = json.loads(manifest_path.read_text(encoding="utf-8"))
        companies_disk = len(list((site_root / "companies" / "by-slug").glob("*.json")))
        traj_dir = site_root / "trajectories"
        traj_disk = len(list(traj_dir.glob("*.json"))) if traj_dir.is_dir() else 0
        for label, manifest_n, disk_n in (
            ("issues", man.get("issues_total"), total),
            ("companies", man.get("companies_total"), companies_disk),
            ("trajectories", man.get("trajectories_total"), traj_disk),
        ):
            if manifest_n is not None and manifest_n != disk_n:
                flag(f"manifest.{label}_count_mismatch", f"manifest={manifest_n} disk={disk_n}")

    print(f"=== quality audit — {total:,} issue records ({site_root}) ===\n")
    dup_groups = findings.pop("_dup_groups", [])
    if not findings and not dup_groups:
        print("No issues found.")
        return 0
    for check in sorted(findings):
        slugs = findings[check]
        if not slugs:
            continue
        sev = "🔴" if check.split(".")[0] in ("pricing", "date", "enum", "identity", "file", "manifest") else "🟡"
        print(f"{sev} {check}: {len(slugs)}")
        for s in slugs[:4]:
            print(f"      {s}")
    if dup_groups:
        print(f"\n🟡 dup.same_name_type groups (>1 record same name+type): {len(dup_groups)}")
        for g in dup_groups[:8]:
            print(f"      {g}")

    gate_hits = sorted(c for c in findings if findings[c] and _is_gate_finding(c))
    if gate:
        if gate_hits:
            print("\n❌ GATE FAILED — integrity-critical findings present:")
            for c in gate_hits:
                print(f"      {c}: {findings[c][:1]}")
            return 1
        print("\n✅ GATE PASSED — no integrity-critical findings.")
    return 0


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="site data-quality audit / CI gate.")
    ap.add_argument("--site-root", type=Path, default=DEFAULT_SITE_ROOT)
    ap.add_argument(
        "--gate",
        action="store_true",
        help="Exit non-zero on integrity-critical findings (manifest parity, "
        "unparseable files, bad enums/identity). Use as a CI commit gate.",
    )
    args = ap.parse_args()
    raise SystemExit(main(gate=args.gate, site_root=args.site_root))
