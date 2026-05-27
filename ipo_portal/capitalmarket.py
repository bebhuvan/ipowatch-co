from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup

from .http import HttpClient
from .models import IpoRecord
from .normalize import clean_text, parse_date, parse_float, parse_int, stable_id
from .storage import save_raw_snapshot, utc_now


CAPITALMARKET_REFERER = "https://www.capitalmarket.com/markets/IPOs/ipo-historic-table"
CAPITALMARKET_TABLES = (
    ("ipo_historic_table", "https://www.capitalmarket.com/markets/IPOs/ipo-historic-table"),
    ("sme_historic_table", "https://www.capitalmarket.com/markets/IPOs/sme-historic-table"),
)


@dataclass(frozen=True)
class SavedSnapshot:
    path: Any
    snapshot: dict[str, Any]


def fetch_capitalmarket_history(client: HttpClient, data_dir: Any, max_pages: int = 1000) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    batch_time = utc_now()
    for table_name, url in CAPITALMARKET_TABLES:
        snapshots.extend(fetch_capitalmarket_table(client, data_dir, table_name, url, batch_time, max_pages))
    return snapshots


def fetch_capitalmarket_table(
    client: HttpClient,
    data_dir: Any,
    table_name: str,
    url: str,
    batch_time: datetime,
    max_pages: int,
) -> list[dict[str, Any]]:
    html, status_code, elapsed_ms = request_html(client, "GET", url, referer=CAPITALMARKET_REFERER)
    snapshots: list[dict[str, Any]] = []
    page_endpoints: list[dict[str, Any]] = []
    page_number = 1
    seen_pages: set[int] = set()

    while html and page_number not in seen_pages and len(seen_pages) < max_pages:
        seen_pages.add(page_number)
        endpoint_name = f"{table_name}_page_{page_number:03d}"
        path = save_raw_snapshot(
            data_dir,
            "capitalmarket",
            endpoint_name,
            url,
            html,
            fetched_at=batch_time,
            status_code=status_code,
            elapsed_ms=elapsed_ms,
        )
        snapshots.append(load_saved_snapshot(path))
        page_endpoints.append({"page": page_number, "endpoint": endpoint_name, "url": url})

        next_target = next_page_target(html)
        if not next_target:
            break
        form_data = hidden_form_fields(html)
        form_data["__EVENTTARGET"] = next_target
        form_data["__EVENTARGUMENT"] = ""
        html, status_code, elapsed_ms = request_html(client, "POST", url, referer=url, data=form_data)
        page_number = active_page_number(html) or page_number + 1

    index_path = save_raw_snapshot(
        data_dir,
        "capitalmarket",
        f"{table_name}_index",
        url,
        {"table": table_name, "pages": page_endpoints},
        fetched_at=batch_time,
        status_code=200,
        elapsed_ms=None,
    )
    snapshots.append(load_saved_snapshot(index_path))
    return snapshots


def request_html(client: HttpClient, method: str, url: str, referer: str | None = None, data: dict[str, str] | None = None) -> tuple[str, int, int]:
    headers = {"Accept": "text/html,application/xhtml+xml,*/*"}
    if referer:
        headers["Referer"] = referer
    started = time.monotonic()
    if method == "POST":
        response = client.session.post(url, headers=headers, data=data, timeout=client.timeout)
    else:
        response = client.session.get(url, headers=headers, timeout=client.timeout)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    response.raise_for_status()
    return response.text, response.status_code, elapsed_ms


def load_saved_snapshot(path: Any) -> dict[str, Any]:
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def hidden_form_fields(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    fields: dict[str, str] = {}
    for item in soup.select("input[type=hidden][name]"):
        name = item.get("name")
        if name:
            fields[name] = item.get("value", "")
    return fields


def active_page_number(html: str) -> int | None:
    soup = BeautifulSoup(html, "html.parser")
    active = soup.select_one("#ContentPlaceHolder1_dtpgrGain .ActivePage")
    return parse_int(active.get_text(" ", strip=True)) if active else None


def next_page_target(html: str) -> str | None:
    current = active_page_number(html) or 1
    targets = pagination_targets(html)
    preferred_label = str(current + 1)
    return targets.get(preferred_label) or targets.get("Next")


def pagination_targets(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    targets: dict[str, str] = {}
    for link in soup.select("#ContentPlaceHolder1_dtpgrGain a[href]"):
        label = link.get_text(" ", strip=True)
        match = re.search(r"__doPostBack\('([^']+)'", link.get("href") or "")
        if label and match:
            targets[label] = match.group(1)
    return targets


def parse_capitalmarket_ipo_history(snapshot: dict[str, Any], as_of: Any = None) -> list[IpoRecord]:
    meta = snapshot.get("meta", {})
    endpoint = meta.get("endpoint", "")
    body = snapshot.get("body")
    if endpoint.endswith("_index") or not isinstance(body, str):
        return []
    if not endpoint.startswith(("ipo_historic_table_page_", "sme_historic_table_page_")):
        return []
    observed_at = meta["fetched_at"]
    rows = capitalmarket_rows(body, endpoint)
    records: list[IpoRecord] = []
    for row in rows:
        company = clean_text(row.get("company_name"))
        cm_id = clean_text(row.get("capitalmarket_ipo_id") or company)
        if not company or not cm_id:
            continue
        listing_date = parse_date(row.get("listing_date"))
        is_sme = endpoint.startswith("sme_")
        records.append(
            IpoRecord(
                id=stable_id("capitalmarket", cm_id, company, listing_date, "sme" if is_sme else "mainboard"),
                company_name=company,
                source="capitalmarket",
                source_endpoint=endpoint,
                source_record_id=cm_id,
                observed_at=observed_at,
                status="past" if not listing_date or not as_of or listing_date <= as_of.isoformat() else "upcoming",
                exchange_platform="SME" if is_sme else "BSE/NSE",
                security_type="SME" if is_sme else "Equity",
                issue_type="IPO",
                listing_date=listing_date,
                issue_size_text=clean_text(row.get("issue_size")),
                shares_offered=parse_int(row.get("lot_size")),
                subscription_times=parse_float(row.get("subscription_total")),
                issue_price=parse_float(row.get("offer_price")),
                listing_day_close=parse_float(row.get("listing_close") or row.get("list_price")),
                listing_open_gain=parse_float(row.get("listing_gain_percent")),
                listing_day_gain=parse_float(row.get("listing_gain_percent")),
                current_price=parse_float(row.get("cmp_nse") or row.get("cmp_bse")),
                gain_loss=parse_float(row.get("current_gain_percent")),
                stock_url=clean_text(row.get("synopsis_url")),
                raw=row,
            )
        )
    return records


def capitalmarket_rows(html: str, endpoint: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []
    is_sme = endpoint.startswith("sme_")
    for table_row in soup.select("tbody tr"):
        cells = [cell.get_text(" ", strip=True) for cell in table_row.find_all("td")]
        if (is_sme and len(cells) < 11) or (not is_sme and len(cells) < 13):
            continue
        link = table_row.select_one('a[href*="IPO-Synopsis"]')
        if not link:
            continue
        company = clean_text(link.find_parent("td").get("title") if link.find_parent("td") else None) or clean_text(link.get_text(" ", strip=True))
        href = link.get("href") or ""
        match = re.search(r"/IPO-Synopsis/(\d+)", href, flags=re.I)
        row: dict[str, Any] = {
            "listing_date": cells[0],
            "company_name": company,
            "capitalmarket_ipo_id": match.group(1) if match else None,
            "synopsis_url": f"https://www.capitalmarket.com{href}" if href.startswith("/") else href,
        }
        if is_sme:
            row.update(
                {
                    "lot_size": cells[2],
                    "issue_size": cells[3],
                    "offer_price": cells[4],
                    "listing_open": cells[5],
                    "listing_close": cells[6],
                    "listing_gain_percent": cells[7],
                    "cmp_bse": cells[8],
                    "cmp_nse": cells[9],
                    "current_gain_percent": cells[10],
                }
            )
        else:
            row.update(
                {
                    "issue_size": cells[2],
                    "subscription_qib": cells[3],
                    "subscription_nii": cells[4],
                    "subscription_retail": cells[5],
                    "subscription_total": cells[6],
                    "offer_price": cells[7],
                    "list_price": cells[8],
                    "listing_gain_percent": cells[9],
                    "cmp_bse": cells[10],
                    "cmp_nse": cells[11],
                    "current_gain_percent": cells[12],
                }
            )
        rows.append(row)
    return rows
