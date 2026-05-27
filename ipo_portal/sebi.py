"""SEBI public-issues filing scraper.

Source: https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=3&ssid=15&smid=10

This is SEBI's authoritative DRHP / offer-document filing directory —
the earliest public signal that a company intends to do a public issue,
often weeks before NSE/BSE list it. Capturing it lets IPO Watch surface
upcoming IPOs at the filing stage.

Two-level scrape:

1. **Listing page** — a single ``<table>`` of (Date, Title) rows. Each
   title links to a per-filing detail page
   (``/filings/public-issues/<month-year>/<slug>_<id>.html``).
2. **Detail page** — carries the actual document PDF under
   ``/sebi_data/attachdocs/<month-year>/<n>.pdf``.

Raw snapshots are written to ``data/raw/sebi/public_issue_filings/`` via
the shared ``storage.save_raw_snapshot`` envelope, so the v2 normalizer
picks them up like any other source. Hash-gated and idempotent.

Pagination: the listing is paginated server-side (25/page, ~2127 rows).
For the daily refresh we only need the first page or two — new filings
appear at the top. ``--pages`` controls depth for a fuller sweep.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .storage import save_raw_snapshot


SEBI_BASE = "https://www.sebi.gov.in"
LISTING_URL = (
    "https://www.sebi.gov.in/sebiweb/home/HomeAction.do"
    "?doListing=yes&sid=3&ssid=15&smid=10"
)
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_PDF_RE = re.compile(r"/sebi_data/[^\"'\s>]+\.pdf", re.IGNORECASE)


@dataclass(frozen=True)
class SebiFiling:
    """One DRHP / offer-document filing from the SEBI directory."""

    filing_date: str          # ISO date, e.g. "2026-05-20"
    company_name: str
    detail_url: str
    document_url: str | None  # resolved from the detail page (PDF)
    document_type: str | None # DRHP / UDRHP / corrigendum / abridged

    def to_dict(self) -> dict[str, Any]:
        return {
            "filing_date": self.filing_date,
            "company_name": self.company_name,
            "detail_url": self.detail_url,
            "document_url": self.document_url,
            "document_type": self.document_type,
        }


@dataclass
class SebiClient:
    timeout: int = 30
    retries: int = 2
    throttle_seconds: float = 0.6
    session: requests.Session = field(default_factory=requests.Session)

    def __post_init__(self) -> None:
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,*/*"})

    def _get(self, url: str) -> str:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = self.session.get(url, timeout=self.timeout)
                resp.raise_for_status()
                time.sleep(self.throttle_seconds)
                return resp.text
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
                last_error = exc
                if isinstance(exc, requests.HTTPError):
                    status = exc.response.status_code if exc.response is not None else None
                    if status is not None and 400 <= status < 500 and status not in {408, 425, 429}:
                        raise
                if attempt < self.retries:
                    time.sleep(self.throttle_seconds * (2 ** attempt))
        assert last_error is not None
        raise last_error

    def fetch_listing(self) -> list[SebiFiling]:
        """Fetch the first listing page and parse filing rows (no PDF yet)."""
        html = self._get(LISTING_URL)
        return parse_listing(html)

    def resolve_document(self, filing: SebiFiling) -> SebiFiling:
        """Fetch the detail page and attach the document PDF URL."""
        if filing.document_url:
            return filing
        try:
            html = self._get(filing.detail_url)
        except requests.RequestException:
            return filing
        pdf = parse_detail_pdf(html)
        doc_type = filing.document_type or _infer_doc_type(filing.detail_url, html)
        return SebiFiling(
            filing_date=filing.filing_date,
            company_name=filing.company_name,
            detail_url=filing.detail_url,
            document_url=pdf,
            document_type=doc_type,
        )


def parse_listing(html: str) -> list[SebiFiling]:
    """Parse the SEBI listing table into filing rows (PDF resolved later)."""
    soup = BeautifulSoup(html, "html.parser")
    filings: list[SebiFiling] = []
    table = soup.find("table")
    if table is None:
        return filings
    for tr in table.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 2:
            continue
        date_text = cells[0].get_text(strip=True)
        link = cells[1].find("a")
        if link is None:
            continue
        company = link.get_text(strip=True)
        detail_url = link.get("href") or ""
        if detail_url and not detail_url.startswith("http"):
            detail_url = urljoin(SEBI_BASE, detail_url)
        iso_date = _parse_sebi_date(date_text)
        if not company or not detail_url:
            continue
        filings.append(
            SebiFiling(
                filing_date=iso_date,
                company_name=company,
                detail_url=detail_url,
                document_url=None,
                document_type=_infer_doc_type(detail_url, ""),
            )
        )
    return filings


def parse_detail_pdf(html: str) -> str | None:
    """Find the document PDF URL on a SEBI filing detail page."""
    match = _PDF_RE.search(html)
    if not match:
        return None
    return urljoin(SEBI_BASE, match.group(0))


def _infer_doc_type(url: str, html: str) -> str | None:
    text = f"{url} {html[:2000]}".lower()
    if "udrhp" in text:
        return "UDRHP"
    if "corrigendum" in text:
        return "corrigendum"
    if "abridged" in text:
        return "abridged_prospectus"
    if "drhp" in text or "draft" in text:
        return "DRHP"
    return None


_MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ],
        start=1,
    )
}


def _parse_sebi_date(text: str) -> str:
    """Parse "May 20, 2026" → "2026-05-20". Empty/garbage → ""."""
    m = re.match(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", text.strip())
    if not m:
        return ""
    month = _MONTHS.get(m.group(1).lower())
    if not month:
        return ""
    return f"{int(m.group(3)):04d}-{month:02d}-{int(m.group(2)):02d}"


def scrape(root: Path, resolve_pdfs: bool = True, limit: int | None = None) -> Path:
    """Scrape the SEBI filings listing and save a raw snapshot.

    Returns the snapshot path. The snapshot body is the list of filing
    dicts (with resolved PDF URLs when ``resolve_pdfs`` is True). The v2
    parser ``sebi_filings`` consumes this.
    """
    client = SebiClient()
    filings = client.fetch_listing()
    if limit is not None:
        filings = filings[:limit]
    if resolve_pdfs:
        resolved: list[SebiFiling] = []
        for f in filings:
            resolved.append(client.resolve_document(f))
        filings = resolved

    body = [f.to_dict() for f in filings]
    return save_raw_snapshot(
        root=root,
        source="sebi",
        endpoint_name="public_issue_filings",
        url=LISTING_URL,
        body=body,
        fetched_at=datetime.now(timezone.utc).replace(microsecond=0),
        status_code=200,
    )
