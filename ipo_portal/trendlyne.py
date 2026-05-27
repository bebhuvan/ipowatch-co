from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from .http import HttpClient
from .storage import save_raw_snapshot, utc_now


TRENDLYNE_REFERER = "https://trendlyne.com/ipo/dashboard/"
TRENDLYNE_BASE = "https://trendlyne.com"
TRENDLYNE_START_YEAR = 2018


def fetch_trendlyne_ipo_data(client: HttpClient, data_dir: Path, as_of: date) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    fetched_at = utc_now()
    endpoints = [
        ("upcoming", f"{TRENDLYNE_BASE}/ipo/api/upcoming/"),
        *[
            (f"year_{year}", f"{TRENDLYNE_BASE}/ipo/api/screener-v2/year/{year}/")
            for year in range(TRENDLYNE_START_YEAR, as_of.year + 1)
        ],
    ]

    for endpoint_name, url in endpoints:
        result = trendlyne_get(client, url)
        path = save_raw_snapshot(
            data_dir,
            "trendlyne",
            endpoint_name,
            result.url,
            result.body,
            fetched_at=fetched_at,
            status_code=result.status_code,
            elapsed_ms=result.elapsed_ms,
        )
        snapshots.append(json.loads(path.read_text(encoding="utf-8")))
    return snapshots


def trendlyne_get(client: HttpClient, url: str) -> Any:
    client.session.headers.update(
        {
            "Accept": "application/json,text/plain,*/*",
            "Referer": TRENDLYNE_REFERER,
            "X-Requested-With": "XMLHttpRequest",
        }
    )
    return client.get(url, referer=TRENDLYNE_REFERER, expect_json=True)
