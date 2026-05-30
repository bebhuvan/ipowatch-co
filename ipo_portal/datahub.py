"""IndiaDataHub Capital Raising fetcher.

Pulls SEBI-sourced monthly time series for IPO/FPO/OFS/Rights/QIP/Preferential
counts and ₹-raised values from the Economic Monitor API, then aggregates to
annual rollups and writes a single normalized JSON under data/site/datahub/.

The API key is read from the INDIA_DATAHUB_API_KEY env var (set in .env, which
is gitignored). Never log or echo the key — pass it through requests params only.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

API_BASE = "https://feeds.indiadatahub.com"

# Indicators in the Capital Markets → Capital Raising sub-category we care about.
# Keys are short stable names we use internally; values are the IndiaDataHub
# series identifiers from /em/filter?category=Capital Markets&subcategory=Capital Raising&db=IND.
INDICATORS: dict[str, dict[str, str]] = {
    "issues_total_count":      {"id": "CMEQCRNTOT11M", "title": "Number of Equity Capital Issues"},
    "public_issues_count":     {"id": "CMEQCRNPBI11M", "title": "Number of Public Issues"},
    "ipo_count":               {"id": "CMEQCRNIPO11M", "title": "Number of IPOs"},
    "ipo_mainboard_count":     {"id": "CMEQCRNIPM11M", "title": "Number of IPOs — Mainboard"},
    "ipo_sme_count":           {"id": "CMEQCRNIPS11M", "title": "Number of IPOs — SME"},
    "fpo_count":               {"id": "CMEQCRNFPO11M", "title": "Number of FPOs"},
    "rights_count":            {"id": "CMEQCRNRGT11M", "title": "Number of Rights Issues"},
    "qip_count":               {"id": "CMEQCRNQIP11M", "title": "Number of QIPs"},
    "preferential_count":      {"id": "CMEQCRNPRF11M", "title": "Number of Preferential Allotments"},
    "total_raised_cr":         {"id": "CMEQCRVTOT11M", "title": "Total Equity Capital Raised (₹ Cr)"},
    "public_issues_raised_cr": {"id": "CMEQCRVPBI11M", "title": "Public Issues Raised (₹ Cr)"},
    "ipo_raised_cr":           {"id": "CMEQCRVIPO11M", "title": "IPOs Raised (₹ Cr)"},
    "ipo_mainboard_raised_cr": {"id": "CMEQCRVIPM11M", "title": "IPOs Raised — Mainboard (₹ Cr)"},
    "ipo_sme_raised_cr":       {"id": "CMEQCRVIPS11M", "title": "IPOs Raised — SME (₹ Cr)"},
    "fpo_raised_cr":           {"id": "CMEQCRVFPO11M", "title": "FPOs Raised (₹ Cr)"},
    "rights_raised_cr":        {"id": "CMEQCRVRGT11M", "title": "Rights Raised (₹ Cr)"},
    "qip_raised_cr":           {"id": "CMEQCRVQIP11M", "title": "QIPs Raised (₹ Cr)"},
    "preferential_raised_cr":  {"id": "CMEQCRVPRF11M", "title": "Preferential Raised (₹ Cr)"},
}


def _load_dotenv(repo_root: Path) -> None:
    """Best-effort .env loader — no external dep on python-dotenv."""
    env_path = repo_root / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _fetch_series(identifier: str, api_key: str, *, from_date: str = "2010-01-01", to_date: str = "2099-12-31") -> list[dict]:
    """Fetch one indicator's monthly series for IND. Returns the Data list."""
    params = {
        "Identifier": identifier,
        "Regions": "IND",
        "From_date": from_date,
        "To_date": to_date,
        "api_key": api_key,
    }
    r = requests.get(f"{API_BASE}/em/data", params=params, timeout=30)
    r.raise_for_status()
    payload = r.json()
    if not isinstance(payload, list) or not payload:
        return []
    return payload[0].get("Data", [])


def _annualize(monthly: list[dict]) -> dict[str, float]:
    """Sum monthly values to annual totals keyed by year string."""
    totals: dict[str, float] = {}
    for obs in monthly:
        d = obs.get("date") or ""
        v = obs.get("value")
        if not d or v is None:
            continue
        year = d[:4]
        # SEBI sometimes posts negative correction values; treat as zero contribution
        # rather than dropping the month entirely (preserves the timestamp).
        if v < 0:
            v = 0.0
        totals[year] = totals.get(year, 0.0) + float(v)
    return totals


def build_capital_raising(api_key: str | None = None) -> dict:
    """Fetch all indicators and return a normalized dict ready to write."""
    if api_key is None:
        api_key = os.environ.get("INDIA_DATAHUB_API_KEY")
    if not api_key:
        raise RuntimeError(
            "INDIA_DATAHUB_API_KEY not set. Add it to .env (gitignored) and re-run."
        )

    out: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "IndiaDataHub Economic Monitor (SEBI)",
        "frequency": "Monthly, with annual rollups",
        "indicators": {},
        "annual": {},
    }

    for key, meta in INDICATORS.items():
        ident = meta["id"]
        monthly = _fetch_series(ident, api_key)
        out["indicators"][key] = {
            "id": ident,
            "title": meta["title"],
            "monthly": monthly,
        }
        annual = _annualize(monthly)
        for year, total in annual.items():
            row = out["annual"].setdefault(year, {})
            if key.endswith("_count"):
                row[key] = int(round(total))
            elif key.endswith("_raised_cr"):
                # API delivers raw ₹; convert to ₹ Crores (1 Cr = 1e7) and round to int.
                row[key] = int(round(total / 1e7))
            else:
                row[key] = round(total, 2)
        # Also convert monthly raised series to ₹ Cr in-place so downstream is consistent.
        if key.endswith("_raised_cr"):
            for obs in out["indicators"][key]["monthly"]:
                if obs.get("value") is not None:
                    obs["value"] = round(obs["value"] / 1e7, 2)
        time.sleep(0.1)  # gentle rate limiting

    # Sort annual rows by year ascending for stable diffs.
    out["annual"] = {y: out["annual"][y] for y in sorted(out["annual"].keys())}
    return out


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    repo_root = Path(__file__).resolve().parents[1]
    _load_dotenv(repo_root)

    # Primary output: data/derived/ is tracked in git and deployed with the site.
    # Legacy output: data/site/datahub/ kept for local compatibility (gitignored).
    payload = json.dumps(build_capital_raising(), indent=2)

    derived_path = repo_root / "data" / "derived" / "capital_raising.json"
    derived_path.parent.mkdir(parents=True, exist_ok=True)
    derived_path.write_text(payload)
    print(f"[datahub] wrote {derived_path}  (primary)", file=sys.stderr)

    legacy_path = repo_root / "data" / "site" / "datahub" / "capital_raising.json"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text(payload)
    print(f"[datahub] wrote {legacy_path}  (legacy)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
