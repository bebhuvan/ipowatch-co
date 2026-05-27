from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .http import HttpClient
from .normalize import clean_text, parse_float, parse_int, slugify
from .storage import save_raw_snapshot, utc_now, write_json


PRIME_BASE_URL = "https://primedatabase.com/"
PRIME_REFERER = "https://primedatabase.com/default.asp"


@dataclass(frozen=True)
class PrimeDemoPage:
    endpoint: str
    path: str
    label: str
    category: str
    relevance: str

    @property
    def url(self) -> str:
        return urljoin(PRIME_BASE_URL, self.path)


PRIME_DEMO_PAGES = (
    PrimeDemoPage("public_issues", "pub_demo.asp", "Public Issues (IPOs, FPOs & OFS (SE))", "primary_equity", "core"),
    PrimeDemoPage("sme_issues", "sme_ipo_demo.asp", "SME Issues (IPOs & FPOs)", "primary_equity", "core"),
    PrimeDemoPage("rights_issues", "rig_demo.asp", "Rights Issues", "primary_equity", "core"),
    PrimeDemoPage("public_debt_issues", "pub_debt_demo.asp", "Public Debt Issues", "primary_debt", "adjacent"),
    PrimeDemoPage("qip", "qual_demo.asp", "Qualified Institutional Placements", "primary_equity", "adjacent"),
    PrimeDemoPage("ipp", "ipp_demo.asp", "Institutional Placement Programmes", "primary_equity", "adjacent"),
    PrimeDemoPage("investment_trusts", "invit_demo.asp", "Investment Trusts (InvITs / ReITs)", "primary_equity", "core"),
    PrimeDemoPage("social_stock_exchange", "SSE_demo.asp", "Social Stock Exchange Issues", "primary_equity", "adjacent"),
    PrimeDemoPage("idrs", "idr_demo.asp", "IDRs", "primary_equity", "adjacent"),
    PrimeDemoPage("takeover_open_offers", "too_demo.asp", "Takeover Open Offers", "secondary_corporate_action", "core"),
    PrimeDemoPage("delisting_offers", "deli_demo.asp", "Delisting Offers", "secondary_corporate_action", "core"),
    PrimeDemoPage("buyback_offers", "buy_demo.asp", "Buyback Offers", "secondary_corporate_action", "core"),
    PrimeDemoPage("preferential_equity", "pref_demo.asp", "Preferential Equity Issues", "primary_equity", "adjacent"),
    PrimeDemoPage("preference_shares", "prefnsdl_demo.asp", "Preference Shares", "primary_equity", "adjacent"),
    PrimeDemoPage("debt_private_placements", "debt_demo.asp", "Debt Private Placements", "primary_debt", "adjacent"),
    PrimeDemoPage("commercial_paper", "cp_demo.asp", "Commercial Paper", "primary_debt", "adjacent"),
    PrimeDemoPage("certificate_of_deposit", "cd_demo.asp", "Certificate of Deposit", "primary_debt", "adjacent"),
    PrimeDemoPage("overseas_offerings", "ocm_demo.asp", "Overseas Offerings", "primary_equity", "adjacent"),
    PrimeDemoPage("block_deals", "blockdeal_demo.asp", "Block Deals", "secondary_market", "adjacent"),
)


def fetch_prime_demo_pages(client: HttpClient, data_dir: Path) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    fetched_at = utc_now()
    for page in PRIME_DEMO_PAGES:
        result = client.get(page.url, referer=PRIME_REFERER, expect_json=False)
        path = save_raw_snapshot(
            data_dir,
            "prime",
            f"demo_{page.endpoint}",
            result.url,
            result.body,
            fetched_at=fetched_at,
            status_code=result.status_code,
            elapsed_ms=result.elapsed_ms,
        )
        snapshots.append(json.loads(path.read_text(encoding="utf-8")))
    index_path = save_raw_snapshot(
        data_dir,
        "prime",
        "demo_index",
        PRIME_REFERER,
        {"pages": [page.__dict__ | {"url": page.url} for page in PRIME_DEMO_PAGES]},
        fetched_at=fetched_at,
        status_code=200,
        elapsed_ms=None,
    )
    snapshots.append(json.loads(index_path.read_text(encoding="utf-8")))
    return snapshots


def build_prime_site_data(site_root: Path, snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    pages = []
    for snapshot in snapshots:
        parsed = parse_prime_demo_snapshot(snapshot)
        if parsed:
            pages.append(parsed)
    pages = sorted(pages, key=lambda item: (item["relevance"], item["category"], item["label"]))
    coverage_rows = [
        {
            "endpoint": page["endpoint"],
            "label": page["label"],
            "category": page["category"],
            "relevance": page["relevance"],
            **row,
        }
        for page in pages
        for row in page["coverage_rows"]
    ]
    summary = {
        "source": "prime",
        "source_name": "PRIME Database",
        "page_count": len(pages),
        "coverage_row_count": len(coverage_rows),
        "core_pages": [page for page in pages if page["relevance"] == "core"],
        "adjacent_pages": [page for page in pages if page["relevance"] != "core"],
    }
    write_json(site_root / "prime" / "demo_pages.json", pages)
    write_json(site_root / "prime" / "coverage.json", coverage_rows)
    write_json(site_root / "prime" / "summary.json", summary)
    return {
        "page_count": len(pages),
        "coverage_row_count": len(coverage_rows),
        "paths": {
            "prime_demo_pages": "prime/demo_pages.json",
            "prime_coverage": "prime/coverage.json",
            "prime_summary": "prime/summary.json",
        },
    }


def parse_prime_demo_snapshot(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    meta = snapshot.get("meta", {})
    endpoint = meta.get("endpoint", "")
    body = snapshot.get("body")
    if meta.get("source") != "prime" or not str(endpoint).startswith("demo_") or endpoint == "demo_index" or not isinstance(body, str):
        return None
    page = page_for_endpoint(endpoint.removeprefix("demo_"))
    soup = BeautifulSoup(body, "html.parser")
    content = soup.select_one(".home-content.inner-page") or soup.body or soup
    title, coverage_text = title_and_coverage(content)
    coverage_rows, total = parse_coverage_table(content)
    services = parse_services(content)
    return {
        "source": "prime",
        "endpoint": endpoint,
        "url": meta.get("url"),
        "observed_at": meta.get("fetched_at"),
        "label": page.label if page else clean_text(title) or endpoint,
        "category": page.category if page else "unknown",
        "relevance": page.relevance if page else "unknown",
        "title": clean_text(title),
        "coverage": coverage_text,
        "coverage_rows": coverage_rows,
        "coverage_total": total,
        "service_modules": services,
    }


def page_for_endpoint(endpoint: str) -> PrimeDemoPage | None:
    for page in PRIME_DEMO_PAGES:
        if page.endpoint == endpoint:
            return page
    return None


def title_and_coverage(content: Any) -> tuple[str | None, str | None]:
    text = " ".join(content.get_text(" ", strip=True).split())
    title = None
    coverage = None
    title_match = re.search(r"(SERVICES PROVIDED UNDER .*?)(?:DATABASE COVERAGE:|Year\s+Amount|Year\s+No\.)", text, flags=re.I)
    if title_match:
        title = title_match.group(1)
    coverage_match = re.search(r"DATABASE COVERAGE:\s*(.*?)(?:Year\s+Amount|Year\s+No\.)", text, flags=re.I)
    if coverage_match:
        coverage = coverage_match.group(1)
    return clean_text(title), clean_text(coverage)


def parse_coverage_table(content: Any) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    table = content.find("table")
    if not table:
        return [], None
    rows = []
    headers: list[str] = []
    total = None
    for tr in table.find_all("tr"):
        cells = [clean_text(cell.get_text(" ", strip=True)) for cell in tr.find_all(["th", "td"])]
        cells = [cell for cell in cells if cell]
        if not cells:
            continue
        if not headers and any("year" in cell.lower() for cell in cells):
            headers = cells
            continue
        if len(cells) < 2:
            continue
        row = coverage_row(cells, headers)
        if not row:
            continue
        if str(row.get("fiscal_year", "")).lower() == "total":
            total = row
        else:
            rows.append(row)
    return rows, total


def coverage_row(cells: list[str], headers: list[str]) -> dict[str, Any] | None:
    year = clean_text(cells[0])
    if not year:
        return None
    row = {"fiscal_year": year, "raw": cells}
    as_on_match = re.search(r"\(as on ([^)]+)\)", year, flags=re.I)
    if as_on_match:
        row["as_on"] = as_on_match.group(1)
        row["fiscal_year"] = clean_text(re.sub(r"\s*\(as on [^)]+\)", "", year, flags=re.I)) or year
    amount_index = next((idx for idx, header in enumerate(headers) if "amount" in header.lower()), None)
    count_index = next((idx for idx, header in enumerate(headers) if "no." in header.lower()), None)
    if amount_index is not None and amount_index < len(cells):
        row["amount_rs_cr"] = parse_float(cells[amount_index])
    elif len(cells) >= 3:
        row["amount_rs_cr"] = parse_float(cells[1])
    if count_index is not None and count_index < len(cells):
        row["count"] = parse_int(cells[count_index])
        row["count_label"] = headers[count_index]
    else:
        row["count"] = parse_int(cells[-1])
        row["count_label"] = "No. of Issues"
    return row


def parse_services(content: Any) -> list[dict[str, str]]:
    services: list[dict[str, str]] = []
    for item in content.find_all(["p", "h3"]):
        if item.name == "h3":
            title = clean_text(item.get_text(" ", strip=True))
            if title:
                services.append({"title": title, "description": ""})
            continue
        strong = item.find("strong")
        if not strong:
            continue
        title = clean_text(strong.get_text(" ", strip=True))
        text = clean_text(item.get_text(" ", strip=True))
        if not title or not text:
            continue
        description = clean_text(text.replace(title, "", 1))
        services.append({"title": title, "description": description or ""})
    seen = set()
    deduped = []
    for service in services:
        key = (slugify(service["title"]), service["description"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(service)
    return deduped
