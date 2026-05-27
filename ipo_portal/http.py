from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import requests


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


@dataclass(frozen=True)
class HttpResult:
    url: str
    status_code: int
    headers: dict[str, str]
    body: Any
    elapsed_ms: int


class HttpClient:
    def __init__(self, timeout: int = 30, retries: int = 2, backoff_seconds: float = 1.0) -> None:
        self.timeout = timeout
        self.retries = retries
        self.backoff_seconds = backoff_seconds
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept": "application/json,text/plain,*/*",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )

    def warm_nse(self) -> None:
        self.get(
            "https://www.nseindia.com/market-data/all-upcoming-issues-ipo",
            referer="https://www.nseindia.com/",
            expect_json=False,
        )

    def get(self, url: str, referer: str | None = None, expect_json: bool = True) -> HttpResult:
        headers = {}
        if referer:
            headers["Referer"] = referer

        started = time.monotonic()
        response: requests.Response | None = None
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = self.session.get(url, headers=headers, timeout=self.timeout)
                response.raise_for_status()
                break
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
            except requests.HTTPError as exc:
                last_error = exc
                status = exc.response.status_code if exc.response is not None else None
                if status is not None and 400 <= status < 500 and status not in {408, 425, 429}:
                    raise
            if attempt < self.retries:
                time.sleep(self.backoff_seconds * (2 ** attempt))
        else:
            assert last_error is not None
            raise last_error

        assert response is not None
        elapsed_ms = int((time.monotonic() - started) * 1000)

        if expect_json:
            try:
                body: Any = response.json()
            except json.JSONDecodeError as exc:
                preview = response.text[:200].replace("\n", " ")
                raise RuntimeError(f"Expected JSON from {url}, got: {preview}") from exc
        else:
            body = response.text

        return HttpResult(
            url=url,
            status_code=response.status_code,
            headers=dict(response.headers),
            body=body,
            elapsed_ms=elapsed_ms,
        )
