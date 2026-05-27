from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .http import HttpClient, HttpResult
from .storage import save_raw_snapshot, utc_now


MONEYCONTROL_REFERER = "https://www.moneycontrol.com/ipo/listed-ipos/"
MONEYCONTROL_LISTED_IPOS_URL = "https://api.moneycontrol.com/mcapi/v1/ipo/get-listed-ipo"
MONEYCONTROL_PAGE_LIMIT = 20


def fetch_moneycontrol_listed_ipos(
    client: HttpClient,
    data_dir: Path,
    max_pages: int = 1000,
    page_limit: int = MONEYCONTROL_PAGE_LIMIT,
) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    batch_time = utc_now()
    page_index: list[dict[str, Any]] = []

    for page_number in range(max_pages):
        start = page_number * page_limit
        result = moneycontrol_get(client, start, page_limit)
        rows = moneycontrol_listed_rows(result.body)
        endpoint_name = f"listed_ipos_page_{start:06d}"
        path = save_raw_snapshot(
            data_dir,
            "moneycontrol",
            endpoint_name,
            result.url,
            result.body,
            fetched_at=batch_time,
            status_code=result.status_code,
            elapsed_ms=result.elapsed_ms,
        )
        snapshots.append(load_saved_snapshot(path))
        page_index.append(
            {
                "page": page_number + 1,
                "start": start,
                "limit": page_limit,
                "endpoint": endpoint_name,
                "url": result.url,
                "row_count": len(rows),
                "first_listing_date": rows[0].get("listing_date") if rows else None,
                "last_listing_date": rows[-1].get("listing_date") if rows else None,
            }
        )
        if len(rows) < page_limit:
            break
        time.sleep(0.1)

    index_path = save_raw_snapshot(
        data_dir,
        "moneycontrol",
        "listed_ipos_index",
        MONEYCONTROL_LISTED_IPOS_URL,
        {"pages": page_index, "limit": page_limit},
        fetched_at=batch_time,
        status_code=200,
        elapsed_ms=None,
    )
    snapshots.append(load_saved_snapshot(index_path))
    return snapshots


def moneycontrol_get(client: HttpClient, start: int, limit: int) -> HttpResult:
    client.session.headers.update(
        {
            "Accept": "application/json,text/plain,*/*",
            "Origin": "https://www.moneycontrol.com",
            "Referer": MONEYCONTROL_REFERER,
        }
    )
    url = f"{MONEYCONTROL_LISTED_IPOS_URL}?start={start}&limit={limit}"
    return client.get(url, referer=MONEYCONTROL_REFERER, expect_json=True)


def moneycontrol_listed_rows(body: Any) -> list[dict[str, Any]]:
    if not isinstance(body, dict) or body.get("success") not in {1, "1", True}:
        return []
    rows = ((body.get("data") or {}).get("listedIpo") or [])
    return [row for row in rows if isinstance(row, dict)]


def load_saved_snapshot(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
