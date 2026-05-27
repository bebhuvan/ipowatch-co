from __future__ import annotations

import re
from datetime import date, datetime
from hashlib import sha1
from typing import Any

from .models import IpoRecord


MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value).replace("\xa0", " ")).strip()
    if not text or text in {"-", "--", "NA", "N/A", "null", "None"}:
        return None
    return text


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def stable_id(source: str, *parts: Any) -> str:
    clean_parts = [clean_text(part) or "" for part in parts]
    readable = slugify("-".join(part for part in clean_parts if part))[:70]
    digest = sha1("|".join([source, *clean_parts]).encode("utf-8")).hexdigest()[:10]
    return f"{readable}-{digest}" if readable else f"{source}-{digest}"


def parse_date(value: Any, default_year: int | None = None) -> str | None:
    text = clean_text(value)
    if not text:
        return None

    def valid(parsed: date) -> str | None:
        # Exchange feeds occasionally contain malformed years such as 0202.
        # Keep those out of normalized records so validation can focus on
        # meaningful market timelines.
        if parsed.year < 1900 or parsed.year > 2100:
            return None
        return parsed.isoformat()

    text = text.replace(".", "")
    if "T" in text:
        try:
            return valid(datetime.fromisoformat(text).date())
        except ValueError:
            pass

    for fmt in (
        "%d-%b-%Y",
        "%d-%B-%Y",
        "%d %b %y",
        "%d %B %y",
        "%d %b, %Y",
        "%d %B, %Y",
        "%d-%m-%Y",
        "%Y-%m-%d",
        "%d-%b-%Y %H:%M:%S",
        "%d-%B-%Y %H:%M:%S",
        "%m/%d/%Y",
        "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%Y %H:%M:%S",
    ):
        try:
            parsed = datetime.strptime(text, fmt).date()
            return valid(parsed)
        except ValueError:
            pass

    match = re.match(r"^(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})$", text)
    if match:
        day = int(match.group(1))
        month = MONTHS.get(match.group(2).lower())
        year = int(match.group(3))
        if month:
            return valid(date(year, month, day))

    match = re.match(r"^([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})$", text)
    if match:
        month = MONTHS.get(match.group(1).lower())
        day = int(match.group(2))
        year = int(match.group(3))
        if month:
            return valid(date(year, month, day))

    match = re.match(r"^(\d{1,2})-([A-Za-z]{3,})-(\d{4})$", text, flags=re.I)
    if match:
        month = MONTHS.get(match.group(2).lower())
        if month:
            return valid(date(int(match.group(3)), month, int(match.group(1))))

    if default_year:
        match = re.match(r"^(\d{1,2})\s+([A-Za-z]+)$", text)
        if match:
            month = MONTHS.get(match.group(2).lower())
            if month:
                return valid(date(default_year, month, int(match.group(1))))

    return None


def listing_date_after_issue_end(listing: str | None, end: str | None) -> str | None:
    if not listing or not end:
        return listing
    try:
        listing_date = date.fromisoformat(listing)
        end_date = date.fromisoformat(end)
    except ValueError:
        return listing
    return listing if listing_date >= end_date else None


def parse_date_range(value: Any) -> tuple[str | None, str | None]:
    text = clean_text(value)
    if not text:
        return None, None
    parts = re.split(r"\s+to\s+|\s*-\s*", text, maxsplit=1, flags=re.I)
    if len(parts) != 2:
        parsed = parse_date(text)
        return parsed, parsed

    start_raw, end_raw = parts
    end = parse_date(end_raw)
    year = int(end[:4]) if end else None
    start = parse_date(start_raw, default_year=year)
    return start, end


def parse_float(value: Any) -> float | None:
    text = clean_text(value)
    if not text:
        return None
    match = re.search(r"-?\d+(?:,\d{2,3})*(?:\.\d+)?|-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def parse_int(value: Any) -> int | None:
    number = parse_float(value)
    return int(number) if number is not None else None


def parse_price_band(value: Any) -> tuple[float | None, float | None, str | None]:
    text = clean_text(value)
    if not text:
        return None, None, None
    numbers = [float(item.replace(",", "")) for item in re.findall(r"\d+(?:,\d{2,3})*(?:\.\d+)?|\d+(?:\.\d+)?", text)]
    if not numbers:
        return None, None, text
    if len(numbers) == 1:
        return numbers[0], numbers[0], text
    return min(numbers[:2]), max(numbers[:2]), text


def normalize_status(source_status: Any, start: str | None, end: str | None, as_of: date | None = None) -> str | None:
    text = (clean_text(source_status) or "").lower()
    if "forth" in text or "upcoming" in text:
        return "upcoming"
    if "active" in text or text == "l":
        return "active"
    if "past" in text or "closed" in text:
        return "past"
    if as_of and start and end:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
        if as_of < start_date:
            return "upcoming"
        if start_date <= as_of <= end_date:
            return "active"
        return "past"
    return clean_text(source_status)


def normalize_nse(snapshot: dict[str, Any], as_of: date | None = None) -> list[IpoRecord]:
    meta = snapshot["meta"]
    endpoint = meta["endpoint"]
    if endpoint in {"offer_documents_equity", "offer_documents_sme"}:
        return normalize_nse_offer_documents(snapshot, as_of)
    if endpoint == "public_issue_advertisements":
        return normalize_nse_public_issue_advertisements(snapshot, as_of)
    if endpoint.startswith("ofs_"):
        return normalize_nse_ofs(snapshot, as_of)
    if endpoint.startswith("tender_"):
        return normalize_nse_tender(snapshot, as_of)
    if endpoint.startswith("rights_"):
        return normalize_nse_rights(snapshot, as_of)
    if endpoint.startswith("ipp_"):
        return normalize_nse_ipp(snapshot, as_of)
    if endpoint.startswith("invits_") or endpoint.startswith("reits_"):
        return normalize_nse_trust_issue(snapshot, as_of)
    if endpoint.startswith("zczp_"):
        return normalize_nse_zczp(snapshot, as_of)
    if endpoint.startswith("lwf_"):
        return normalize_nse_lwf(snapshot, as_of)
    if endpoint not in {"ipo_current_issue", "ipo_upcoming", "ipo_public_past_issues"}:
        return []
    observed_at = meta["fetched_at"]
    body = snapshot["body"]
    records = body if isinstance(body, list) else []
    normalized: list[IpoRecord] = []
    for row in records:
        if not isinstance(row, dict):
            continue
        company = clean_text(row.get("companyName") or row.get("company") or row.get("issue_name"))
        if not company:
            continue
        start = parse_date(row.get("issueStartDate") or row.get("ipoStartDate"))
        end = parse_date(row.get("issueEndDate") or row.get("ipoEndDate"))
        if not start and not end and row.get("dateof_issue"):
            start, end = parse_date_range(row.get("dateof_issue"))
        low, high, price_text = parse_price_band(row.get("priceRange") or row.get("priceBand"))
        listing = listing_date_after_issue_end(parse_date(row.get("listingDate")), end)
        record = IpoRecord(
            id=stable_id("nse", row.get("symbol"), company, start, end, endpoint),
            company_name=company,
            source="nse",
            source_endpoint=endpoint,
            source_record_id=clean_text(row.get("symbol") or row.get("companyName") or company) or company,
            observed_at=observed_at,
            status=normalize_status(row.get("status"), start, end, as_of),
            symbol=clean_text(row.get("symbol")),
            exchange_platform="NSE",
            security_type=clean_text(row.get("securityType") or row.get("series")),
            issue_type="IPO",
            issue_start_date=start,
            issue_end_date=end,
            listing_date=listing,
            price_band_low=low,
            price_band_high=high,
            price_band_text=price_text,
            issue_price=parse_float(row.get("issuePrice")),
            issue_size_text=clean_text(row.get("issue_size")),
            shares_offered=parse_int(row.get("noOfSharesOffered")),
            shares_bid=parse_int(row.get("noOfsharesBid")),
            subscription_times=parse_float(row.get("noOfTime")),
            raw=row,
        )
        normalized.append(record)
    return normalized


def normalize_nse_ofs(snapshot: dict[str, Any], as_of: date | None = None) -> list[IpoRecord]:
    meta = snapshot["meta"]
    endpoint = meta["endpoint"]
    observed_at = meta["fetched_at"]
    body = snapshot["body"]
    rows = nse_ofs_rows(snapshot)
    rows = rows if isinstance(rows, list) else []
    normalized: list[IpoRecord] = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        company = clean_text(row.get("companyName"))
        if not company:
            continue
        symbol = clean_symbol(row.get("symbol") or row.get("symbolRG"))
        category_raw = row.get("category")
        if not category_raw and endpoint == "ofs_active_total_retail":
            category_raw = "total retail"
        category = clean_text(category_raw)
        offer_date = parse_date(row.get("offerDate"))
        status = status_from_endpoint(endpoint, as_of, offer_date, offer_date)
        source_id = clean_text("|".join(part for part in (symbol, category, offer_date) if part) or company) or company
        floor_price = parse_float(row.get("floorPrice"))
        allocated_price = parse_float(row.get("allocatePrice") or row.get("allocatePriceRetail") or row.get("allocatePriceGeneral"))
        normalized.append(
            IpoRecord(
                id=stable_id("nse-ofs", source_id, company, offer_date, endpoint),
                company_name=company,
                source="nse",
                source_endpoint=endpoint,
                source_record_id=source_id,
                observed_at=observed_at,
                status=status,
                symbol=symbol,
                exchange_platform="NSE",
                security_type=category,
                issue_type="OFS",
                issue_start_date=offer_date,
                issue_end_date=offer_date,
                price_band_low=floor_price,
                price_band_high=floor_price,
                price_band_text=clean_text(row.get("floorPrice")),
                issue_price=allocated_price,
                issue_size_text=clean_text(row.get("noOfshareOffered") or row.get("totalRG")),
                shares_offered=parse_int(row.get("noOfshareOffered") or row.get("totalRG")),
                shares_bid=parse_int(row.get("cumlativeQty") or row.get("cumlativeQtyRetailsBid") or row.get("totalIssue")),
                subscription_times=parse_float(row.get("noOfTimes") or row.get("totalNOT")),
                raw=row,
            )
        )
    return normalized


def nse_ofs_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    meta = snapshot["meta"]
    endpoint = meta["endpoint"]
    body = snapshot["body"]
    if not isinstance(body, dict):
        return body if isinstance(body, list) else []
    rows = body.get("data")
    if endpoint != "ofs_active_grouped" or not isinstance(rows, list):
        return rows if isinstance(rows, list) else []
    flattened = []
    for issue in rows:
        if not isinstance(issue, dict):
            continue
        for detail in issue.get("data") or []:
            if not isinstance(detail, dict):
                continue
            row = dict(detail)
            row["symbol"] = issue.get("symbol")
            row["companyName"] = issue.get("company")
            row["category"] = detail.get("series")
            row["noOfshareOffered"] = detail.get("issueSize")
            row["cumlativeQty"] = detail.get("totalSubscription")
            flattened.append(row)
    return flattened


def normalize_nse_tender(snapshot: dict[str, Any], as_of: date | None = None) -> list[IpoRecord]:
    meta = snapshot["meta"]
    endpoint = meta["endpoint"]
    observed_at = meta["fetched_at"]
    body = snapshot["body"]
    rows = body.get("data") if isinstance(body, dict) else body
    rows = rows if isinstance(rows, list) else []
    normalized: list[IpoRecord] = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        company = clean_text(row.get("company"))
        if not company:
            continue
        symbol = clean_symbol(row.get("symbol"))
        start = parse_date(row.get("todStartDate") or row.get("offerDate"))
        end = parse_date(row.get("todEndDate") or row.get("offerEndDate"))
        offer_type = clean_text(row.get("offerType")) or "Tender"
        source_id = clean_text("|".join(part for part in (symbol, start, end, offer_type) if part) or company) or company
        price = parse_float(row.get("floorPrice") or row.get("band") or row.get("allocatedPrice"))
        normalized.append(
            IpoRecord(
                id=stable_id("nse-tender", source_id, company, start, end, endpoint),
                company_name=company,
                source="nse",
                source_endpoint=endpoint,
                source_record_id=source_id,
                observed_at=observed_at,
                status=normalize_status(row.get("status"), start, end, as_of) or status_from_endpoint(endpoint, as_of, start, end),
                symbol=symbol,
                exchange_platform="NSE",
                security_type=clean_text(row.get("issueType") or row.get("series")),
                issue_type=offer_type,
                issue_start_date=start,
                issue_end_date=end,
                price_band_low=price,
                price_band_high=price,
                price_band_text=clean_text(row.get("floorPrice") or row.get("band") or row.get("allocatedPrice")),
                issue_price=parse_float(row.get("allocatedPrice")),
                issue_size_text=clean_text(row.get("issueSize")),
                shares_offered=parse_int(row.get("issueSize")),
                shares_bid=parse_int(row.get("totQty") or row.get("cumDmatQty") or row.get("cumQtyDmat")),
                subscription_times=parse_float(row.get("numberOfTImes") or row.get("numberOfTimes")),
                raw=row,
            )
        )
    return normalized


def normalize_nse_rights(snapshot: dict[str, Any], as_of: date | None = None) -> list[IpoRecord]:
    meta = snapshot["meta"]
    endpoint = meta["endpoint"]
    observed_at = meta["fetched_at"]
    rows = rows_from_body(snapshot["body"])
    normalized: list[IpoRecord] = []
    for row in rows:
        company = clean_text(row.get("company"))
        if not company:
            continue
        start = parse_date(row.get("rightStartDate"))
        end = parse_date(row.get("rightEndDate"))
        symbol = clean_symbol(row.get("symbol"))
        source_id = clean_text("|".join(part for part in (symbol, start, end) if part) or company) or company
        normalized.append(
            IpoRecord(
                id=stable_id("nse-rights", source_id, company, start, end, endpoint),
                company_name=company,
                source="nse",
                source_endpoint=endpoint,
                source_record_id=source_id,
                observed_at=observed_at,
                status=normalize_status(row.get("status"), start, end, as_of) or status_from_endpoint(endpoint, as_of, start, end),
                symbol=symbol,
                exchange_platform="NSE",
                security_type=clean_text(row.get("series")),
                issue_type="Rights",
                issue_start_date=start,
                issue_end_date=end,
                issue_size_text=clean_text(row.get("qty")),
                shares_offered=parse_int(row.get("qty")),
                shares_bid=parse_int(row.get("bidQty") or row.get("nse_bse_cumu")),
                raw=row,
            )
        )
    return normalized


def normalize_nse_ipp(snapshot: dict[str, Any], as_of: date | None = None) -> list[IpoRecord]:
    meta = snapshot["meta"]
    endpoint = meta["endpoint"]
    observed_at = meta["fetched_at"]
    rows = rows_from_body(snapshot["body"])
    normalized: list[IpoRecord] = []
    for row in rows:
        company = clean_text(row.get("company"))
        if not company:
            continue
        start = parse_date(row.get("ippStartDate"))
        end = parse_date(row.get("ippEndDate"))
        symbol = clean_symbol(row.get("symbol"))
        source_id = clean_text("|".join(part for part in (symbol, start, end) if part) or company) or company
        normalized.append(
            IpoRecord(
                id=stable_id("nse-ipp", source_id, company, start, end, endpoint),
                company_name=company,
                source="nse",
                source_endpoint=endpoint,
                source_record_id=source_id,
                observed_at=observed_at,
                status=status_from_endpoint(endpoint, as_of, start, end),
                symbol=symbol,
                exchange_platform="NSE",
                issue_type="IPP",
                issue_start_date=start,
                issue_end_date=end,
                issue_size_text=clean_text(row.get("issueSize")),
                shares_offered=parse_int(row.get("issueSize")),
                shares_bid=parse_int(row.get("ieqQty")),
                raw=row,
            )
        )
    return normalized


def normalize_nse_trust_issue(snapshot: dict[str, Any], as_of: date | None = None) -> list[IpoRecord]:
    meta = snapshot["meta"]
    endpoint = meta["endpoint"]
    observed_at = meta["fetched_at"]
    rows = rows_from_body(snapshot["body"])
    normalized: list[IpoRecord] = []
    issue_type = "InvIT" if endpoint.startswith("invits_") else "REIT"
    for row in rows:
        company = clean_text(row.get("company") or row.get("companyName"))
        if not company:
            continue
        start = parse_date(row.get("ipoStartDate"))
        end = parse_date(row.get("ipoEndDate"))
        symbol = clean_symbol(row.get("symbol"))
        low, high, price_text = parse_price_band(row.get("priceRange"))
        source_id = clean_text("|".join(part for part in (symbol, start, end) if part) or company) or company
        listing = listing_date_after_issue_end(parse_date(row.get("listingDate")), end)
        normalized.append(
            IpoRecord(
                id=stable_id(f"nse-{issue_type.lower()}", source_id, company, start, end, endpoint),
                company_name=company,
                source="nse",
                source_endpoint=endpoint,
                source_record_id=source_id,
                observed_at=observed_at,
                status=status_from_endpoint(endpoint, as_of, start, end),
                symbol=symbol,
                exchange_platform="NSE",
                security_type=clean_text(row.get("securityType")),
                issue_type=issue_type,
                issue_start_date=start,
                issue_end_date=end,
                listing_date=listing,
                price_band_low=low,
                price_band_high=high,
                price_band_text=price_text,
                issue_price=parse_float(row.get("issuePrice")),
                raw=row,
            )
        )
    return normalized


def normalize_nse_zczp(snapshot: dict[str, Any], as_of: date | None = None) -> list[IpoRecord]:
    meta = snapshot["meta"]
    endpoint = meta["endpoint"]
    if endpoint == "zczp_company_list":
        return []
    observed_at = meta["fetched_at"]
    rows = rows_from_body(snapshot["body"])
    normalized: list[IpoRecord] = []
    for row in rows:
        company = clean_text(row.get("companyName") or row.get("company"))
        if not company:
            continue
        start = parse_date(row.get("issueStartDate"))
        end = parse_date(row.get("issueEndDate"))
        symbol = clean_symbol(row.get("symbol"))
        source_id = clean_text("|".join(part for part in (symbol, start, end) if part) or company) or company
        normalized.append(
            IpoRecord(
                id=stable_id("nse-zczp", source_id, company, start, end, endpoint),
                company_name=company,
                source="nse",
                source_endpoint=endpoint,
                source_record_id=source_id,
                observed_at=observed_at,
                status=normalize_status(row.get("status"), start, end, as_of) or status_from_endpoint(endpoint, as_of, start, end),
                symbol=symbol,
                exchange_platform="NSE",
                security_type=clean_text(row.get("securityType") or row.get("series")),
                issue_type="ZCZP",
                issue_start_date=start,
                issue_end_date=end,
                issue_size_text=clean_text(row.get("issueSize")),
                shares_offered=parse_int(row.get("issueSize")),
                raw=row,
            )
        )
    return normalized


def normalize_nse_lwf(snapshot: dict[str, Any], as_of: date | None = None) -> list[IpoRecord]:
    meta = snapshot["meta"]
    endpoint = meta["endpoint"]
    if endpoint == "lwf_company_list":
        return []
    observed_at = meta["fetched_at"]
    rows = rows_from_body(snapshot["body"])
    normalized: list[IpoRecord] = []
    for row in rows:
        company = clean_text(row.get("company") or row.get("companyName"))
        if not company:
            continue
        start = parse_date(row.get("startDate") or row.get("offerStartDate") or row.get("lwfStartDate"))
        end = parse_date(row.get("endDate") or row.get("offerEndDate") or row.get("lwfEndDate"))
        symbol = clean_symbol(row.get("symbol"))
        source_id = clean_text("|".join(part for part in (symbol, start, end) if part) or company) or company
        normalized.append(
            IpoRecord(
                id=stable_id("nse-lwf", source_id, company, start, end, endpoint),
                company_name=company,
                source="nse",
                source_endpoint=endpoint,
                source_record_id=source_id,
                observed_at=observed_at,
                status=status_from_endpoint(endpoint, as_of, start, end),
                symbol=symbol,
                exchange_platform="NSE",
                issue_type="LWF",
                issue_start_date=start,
                issue_end_date=end,
                raw=row,
            )
        )
    return normalized


def rows_from_body(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [row for row in body if isinstance(row, dict)]
    if isinstance(body, dict):
        rows = body.get("data")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def status_from_endpoint(endpoint: str, as_of: date | None, start: str | None = None, end: str | None = None) -> str:
    if "active" in endpoint:
        return "active"
    if "current" in endpoint:
        return "active"
    if "forthcoming" in endpoint:
        return "upcoming"
    if "past" in endpoint:
        return "past"
    return normalize_status(None, start, end, as_of) or "unknown"


def normalize_nse_offer_documents(snapshot: dict[str, Any], as_of: date | None = None) -> list[IpoRecord]:
    meta = snapshot["meta"]
    endpoint = meta["endpoint"]
    observed_at = meta["fetched_at"]
    body = snapshot["body"]
    rows = body if isinstance(body, list) else []
    normalized: list[IpoRecord] = []
    platform = "NSE SME" if endpoint.endswith("_sme") else "NSE"

    for row in rows:
        if not isinstance(row, dict):
            continue
        company = clean_text(row.get("company"))
        if not company:
            continue
        start = parse_date(row.get("issue_open_date"))
        end = parse_date(row.get("issue_close_date"))
        symbol = clean_symbol(row.get("symbol"))
        source_id = clean_text(symbol or row.get("pan_no") or company) or company
        documents = nse_offer_documents(row)
        normalized.append(
            IpoRecord(
                id=stable_id("nse-offer-doc", source_id, company, start, end, endpoint),
                company_name=company,
                source="nse",
                source_endpoint=endpoint,
                source_record_id=source_id,
                observed_at=observed_at,
                status=normalize_status(None, start, end, as_of) or "document",
                symbol=symbol,
                exchange_platform=platform,
                issue_type="IPO",
                issue_start_date=start,
                issue_end_date=end,
                documents=documents,
                raw=row,
            )
        )
    return normalized


def normalize_nse_public_issue_advertisements(snapshot: dict[str, Any], as_of: date | None = None) -> list[IpoRecord]:
    meta = snapshot["meta"]
    endpoint = meta["endpoint"]
    observed_at = meta["fetched_at"]
    body = snapshot["body"]
    rows = body if isinstance(body, list) else []
    normalized: list[IpoRecord] = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        company = clean_text(row.get("issuerName"))
        if not company:
            continue
        draft_date = parse_date(row.get("draftDate"))
        board = clean_text(row.get("boardType"))
        documents = nse_public_issue_ad_documents(row)
        source_id = clean_text(row.get("panNo") or company) or company
        normalized.append(
            IpoRecord(
                id=stable_id("nse-public-ad", source_id, company, draft_date, endpoint),
                company_name=company,
                source="nse",
                source_endpoint=endpoint,
                source_record_id=source_id,
                observed_at=observed_at,
                status="document",
                exchange_platform="NSE SME" if board and "SME" in board.upper() else "NSE",
                security_type=board,
                issue_type=clean_text(row.get("issueType")) or "IPO",
                issue_start_date=draft_date,
                documents=documents,
                raw=row,
            )
        )
    return normalized


def clean_symbol(value: Any) -> str | None:
    text = clean_text(value)
    if not text or text == "-":
        return None
    return text


def clean_url(value: Any) -> str | None:
    text = clean_text(value)
    if not text or text == "-":
        return None
    return text.strip()


def nse_offer_documents(row: dict[str, Any]) -> list[dict[str, str]]:
    documents = []
    for url_key, date_key, label in (
        ("drhpAttach", "drhpDate", "DRHP"),
        ("rhpAttach", "rhpDate", "RHP"),
        ("fpAttach", "fpDate", "Final prospectus"),
        ("advAttach", "advDate", "Public issue advertisement"),
        ("drhpAvLink", "drhpSubDate", "Audiovisual DRHP"),
        ("iapAvLink", "iapSubDate", "Audiovisual abridged prospectus"),
        ("icAvLink", "icSubDate", "Audiovisual investor charter"),
        ("ipo_inprincipal_xbrl_link", None, "In-principle approval XBRL"),
        ("ipo_inlisting_xbrl_link", None, "In-listing XBRL"),
        ("ipo_abridged_prospectus_xbrl_link", None, "Abridged prospectus XBRL"),
    ):
        url = clean_url(row.get(url_key))
        if not url:
            continue
        doc = {"type": label, "url": url}
        if date_key:
            doc_date = parse_date(row.get(date_key))
            if doc_date:
                doc["date"] = doc_date
        documents.append(doc)
    return dedupe_documents(documents)


def nse_public_issue_ad_documents(row: dict[str, Any]) -> list[dict[str, str]]:
    documents = []
    for key, value in row.items():
        if not key.startswith("record") or not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            url = clean_url(item.get("attFilename"))
            if not url:
                continue
            doc = {"type": clean_text(item.get("advertisementType")) or "Public issue advertisement", "url": url}
            submit_date = parse_date(item.get("submitDate"))
            if submit_date:
                doc["date"] = submit_date
            documents.append(doc)
    return dedupe_documents(documents)


def dedupe_documents(documents: list[dict[str, str]]) -> list[dict[str, str]]:
    seen = set()
    deduped = []
    for document in documents:
        key = (document.get("type"), document.get("url"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(document)
    return deduped


def normalize_bse(snapshot: dict[str, Any], as_of: date | None = None) -> list[IpoRecord]:
    meta = snapshot["meta"]
    endpoint = meta["endpoint"]
    if endpoint in BSE_DOCUMENT_FEEDS:
        return normalize_bse_document_feed(snapshot, as_of)
    if endpoint == "sgb_live_issues":
        return normalize_bse_sgb(snapshot, as_of)
    if (
        endpoint not in {"public_issue", "public_issue_details", "ipo_documents"}
        and not endpoint.startswith("ipo_performance_")
    ):
        return []
    observed_at = meta["fetched_at"]
    body = snapshot["body"]
    table = body.get("Table") if isinstance(body, dict) else None
    docs_table = body.get("table") if isinstance(body, dict) else None
    normalized: list[IpoRecord] = []

    if isinstance(table, list):
        for row in table:
            if not isinstance(row, dict):
                continue
            if endpoint.startswith("ipo_performance_"):
                normalized.append(normalize_bse_performance_row(row, endpoint, observed_at, as_of))
                continue
            issue_type = clean_text(row.get("IR_flag") or row.get("IR_FLAG_FULL"))
            if issue_type and issue_type.upper() not in {"IPO", "FPO"}:
                continue
            company = clean_text(row.get("Scrip_Name") or row.get("LONG_NAME"))
            if not company:
                continue
            start = parse_date(row.get("Start_Dt"))
            end = parse_date(row.get("End_Dt"))
            low, high, price_text = parse_price_band(row.get("Price_Band"))
            source_id = clean_text(row.get("IPO_NO") or row.get("Scrip_cd") or company) or company
            normalized.append(
                IpoRecord(
                    id=stable_id("bse", source_id, company, start, end, endpoint),
                    company_name=company,
                    source="bse",
                    source_endpoint=endpoint,
                    source_record_id=source_id,
                    observed_at=observed_at,
                    status=normalize_status(row.get("Status"), start, end, as_of),
                    symbol=clean_text(row.get("short_name")),
                    exchange_platform=clean_text(row.get("eXCHANGE_PLATFORM")) or "BSE",
                    security_type=clean_text(row.get("IR_FLAG_FULL")),
                    issue_type=issue_type,
                    issue_start_date=start,
                    issue_end_date=end,
                    price_band_low=low,
                    price_band_high=high,
                    price_band_text=price_text,
                    face_value=parse_float(row.get("Face_Val")),
                    raw=row,
                )
            )

    if isinstance(docs_table, list):
        for row in docs_table:
            if not isinstance(row, dict):
                continue
            company = clean_text(row.get("Scrip_Name"))
            if not company:
                continue
            documents = []
            for key, label in (
                ("DRHP_Doc", "DRHP"),
                ("Red_Herring_Prospectus", "RHP"),
                ("Prospectus", "Prospectus"),
                ("T5Stage_Document", "T+5 document"),
                ("Audiovisual_RHP", "Audiovisual RHP"),
                ("Audiovisual_DRHP", "Audiovisual DRHP"),
            ):
                doc = clean_text(row.get(key))
                if doc:
                    href = doc if doc.startswith("http") else f"https://www.bseindia.com/downloads/ipo/{doc}"
                    documents.append({"type": label, "url": href})
            normalized.append(
                IpoRecord(
                    id=stable_id("bse-doc", row.get("scrip_cd"), company, row.get("updated_date"), endpoint),
                    company_name=company,
                    source="bse",
                    source_endpoint=endpoint,
                    source_record_id=clean_text(row.get("scrip_cd") or company) or company,
                    observed_at=observed_at,
                    status="document",
                    exchange_platform="BSE",
                    issue_type="IPO",
                    documents=documents,
                    raw=row,
                )
            )

    return normalized


BSE_DOCUMENT_FEEDS = {
    "buyback_tender_documents",
    "buyback_open_market_documents",
    "takeover_documents",
    "voluntary_delisting_documents",
    "rights_issue_documents",
    "qip_documents",
    "invit_placement_documents",
    "invit_reit_documents",
    "bond_issue_documents",
}


def normalize_bse_document_feed(snapshot: dict[str, Any], as_of: date | None = None) -> list[IpoRecord]:
    meta = snapshot["meta"]
    endpoint = meta["endpoint"]
    observed_at = meta["fetched_at"]
    rows = bse_rows_from_body(snapshot["body"])
    normalized: list[IpoRecord] = []

    for row in rows:
        company = bse_company_name(row, endpoint)
        if not company:
            continue
        issue_type = bse_document_issue_type(row, endpoint)
        documents = bse_documents_for_endpoint(row, endpoint)
        source_start = bse_document_start_date(row, endpoint, documents)
        source_end = bse_document_end_date(row, endpoint, documents)
        document_dates = sorted(doc.get("date") for doc in documents if doc.get("date"))
        start = document_dates[0] if document_dates else source_start
        source_id = bse_document_source_id(row, endpoint, company, source_start, source_end)
        normalized.append(
            IpoRecord(
                id=stable_id(f"bse-{slugify(issue_type)}", source_id, company, start, source_end, endpoint),
                company_name=company,
                source="bse",
                source_endpoint=endpoint,
                source_record_id=source_id,
                observed_at=observed_at,
                status="document",
                symbol=clean_text(row.get("scripcode") or row.get("scrip_cd") or row.get("COMPANY_CODE")),
                exchange_platform="BSE",
                issue_type=issue_type,
                issue_start_date=start,
                price_band_text=clean_text(row.get("priceband") or row.get("Price_Band")),
                issue_price=parse_float(row.get("FloorPrice") or row.get("IssuePrice") or row.get("issueprice")),
                documents=documents,
                raw=row,
            )
        )
    return normalized


def normalize_bse_sgb(snapshot: dict[str, Any], as_of: date | None = None) -> list[IpoRecord]:
    meta = snapshot["meta"]
    observed_at = meta["fetched_at"]
    normalized: list[IpoRecord] = []
    for row in bse_rows_from_body(snapshot["body"]):
        company = clean_text(row.get("IM_IPO_NAME") or row.get("IssueName"))
        if not company:
            continue
        start = parse_date(row.get("Start_Dt") or row.get("Open_Date"))
        end = parse_date(row.get("End_Dt") or row.get("Close_Date"))
        source_id = clean_text(row.get("SCRIP_CD") or row.get("scrip_cd") or company) or company
        normalized.append(
            IpoRecord(
                id=stable_id("bse-sgb", source_id, company, start, end, "sgb_live_issues"),
                company_name=company,
                source="bse",
                source_endpoint="sgb_live_issues",
                source_record_id=source_id,
                observed_at=observed_at,
                status=normalize_status(row.get("Status"), start, end, as_of) or status_from_endpoint("sgb_live_issues", as_of, start, end),
                symbol=clean_text(row.get("SCRIP_CD") or row.get("scrip_cd")),
                exchange_platform="BSE",
                issue_type="SGB",
                issue_start_date=start,
                issue_end_date=end,
                price_band_low=parse_float(row.get("FloorPrice") or row.get("Floor_Price")),
                price_band_high=parse_float(row.get("FloorPrice") or row.get("Floor_Price")),
                raw=row,
            )
        )
    return normalized


def bse_rows_from_body(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [row for row in body if isinstance(row, dict)]
    if not isinstance(body, dict):
        return []
    for key in ("Table", "table", "Table1", "table1", "Table2", "table2", "data"):
        rows = body.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return [body] if any(clean_text(value) for value in body.values()) else []


def bse_company_name(row: dict[str, Any], endpoint: str) -> str | None:
    return clean_text(
        row.get("Scrip_Name")
        or row.get("Fld_NameOfCompany")
        or row.get("Fld_NameOfTheCompany")
        or row.get("Company_Name")
        or row.get("COMPANY")
        or row.get("name")
    )


def bse_document_issue_type(row: dict[str, Any], endpoint: str) -> str:
    if endpoint.startswith("buyback_"):
        return "Buyback"
    if endpoint == "takeover_documents":
        return "Takeover"
    if endpoint == "voluntary_delisting_documents":
        return "Voluntary Delisting"
    if endpoint == "rights_issue_documents":
        return "Rights"
    if endpoint == "qip_documents":
        return "QIP"
    if endpoint == "invit_placement_documents":
        return "InvIT"
    if endpoint == "invit_reit_documents":
        company = (clean_text(row.get("COMPANY")) or "").upper()
        if "REIT" in company:
            return "REIT"
        if "INVIT" in company:
            return "InvIT"
        return "InvIT/REIT"
    if endpoint == "bond_issue_documents":
        return "Bond"
    return "Public Issue"


def bse_documents_for_endpoint(row: dict[str, Any], endpoint: str) -> list[dict[str, str]]:
    if endpoint in {"buyback_tender_documents", "buyback_open_market_documents"}:
        return dedupe_documents(
            [
                *bse_document_candidates(row, (("predoc", "Pre-offer document", "preti"), ("postdoc", "Post-offer document", "postti"))),
            ]
        )
    if endpoint in {"takeover_documents", "voluntary_delisting_documents"}:
        return dedupe_documents(
            bse_document_candidates(row, (("preDoc", "Pre-offer document", "PreendDate"), ("PostDoc", "Post-offer document", "PostendDate")))
        )
    if endpoint in {"rights_issue_documents", "qip_documents"}:
        return dedupe_documents(
            bse_document_candidates(
                row,
                (("In_Principle_Stage", "In-principle approval", "InPrinciple_date"), ("Listing_Stage", "Listing approval", "Listing_stage_date")),
            )
        )
    if endpoint == "invit_placement_documents":
        return dedupe_documents(bse_document_candidates(row, (("file1", "Issue document", "issuedate"), ("file2", "Draft offer document", "date1"))))
    if endpoint == "invit_reit_documents":
        return dedupe_documents(
            bse_document_candidates(
                row,
                (
                    ("Draft_FILE", "Draft offer document", "Draft_DTTM"),
                    ("Red_Herring_FILE", "Red herring prospectus", "Red_Herring_DTTM"),
                    ("Prospectus_FILE", "Prospectus", "Prospectus_DTTM"),
                ),
            )
        )
    if endpoint == "bond_issue_documents":
        return dedupe_documents(
            bse_document_candidates(
                row,
                (("Red_Herring_Prospectus", "Draft prospectus", "DRHP_Date"), ("Prospectus", "Prospectus", "Open_Date")),
            )
        )
    return []


def bse_document_candidates(row: dict[str, Any], specs: tuple[tuple[str, str, str | None], ...]) -> list[dict[str, str]]:
    documents: list[dict[str, str]] = []
    for url_key, label, date_key in specs:
        url = bse_document_url(row.get(url_key))
        if not url:
            continue
        document = {"type": label, "url": url}
        doc_date = parse_date(row.get(date_key)) if date_key else None
        if doc_date:
            document["date"] = doc_date
        status = clean_text(row.get(url_key.replace("doc", "Status")) or row.get(url_key.replace("Doc", "Status")))
        if status:
            document["status"] = status
        documents.append(document)
    return documents


def bse_document_url(value: Any) -> str | None:
    text = clean_url(value)
    if not text:
        return None
    if text.startswith("http"):
        return text
    if text.startswith("/"):
        return f"https://www.bseindia.com{text}"
    return f"https://www.bseindia.com/downloads/ipo/{text}"


def bse_document_start_date(row: dict[str, Any], endpoint: str, documents: list[dict[str, str]]) -> str | None:
    for key in (
        "InPrinciple_date",
        "Draft_DTTM",
        "date1",
        "DRHP_Date",
        "PreendDate",
        "preti",
        "issuedate",
    ):
        parsed = parse_date(row.get(key))
        if parsed:
            return parsed
    dates = sorted(doc.get("date") for doc in documents if doc.get("date"))
    return dates[0] if dates else None


def bse_document_end_date(row: dict[str, Any], endpoint: str, documents: list[dict[str, str]]) -> str | None:
    for key in (
        "Listing_stage_date",
        "Prospectus_DTTM",
        "PostendDate",
        "postti",
        "Open_Date",
    ):
        parsed = parse_date(row.get(key))
        if parsed:
            return parsed
    dates = sorted(doc.get("date") for doc in documents if doc.get("date"))
    return dates[-1] if dates else None


def bse_document_source_id(row: dict[str, Any], endpoint: str, company: str, start: str | None, end: str | None) -> str:
    base_id = clean_text(
        row.get("scripcode")
        or row.get("scrip_cd")
        or row.get("COMPANY_CODE")
        or row.get("Fld_CompanyId")
        or row.get("Recordid")
        or row.get("DRHP_ID")
        or company
    )
    return clean_text("|".join(part for part in (base_id, start, end, endpoint) if part)) or company


def normalize_bse_performance_row(row: dict[str, Any], endpoint: str, observed_at: str, as_of: date | None = None) -> IpoRecord:
    company = clean_text(row.get("CompanyName")) or "Unknown company"
    listing_date = parse_date(row.get("ListedOn"))
    source_id = clean_text(row.get("Company_Short_Name") or row.get("CompanyName") or company) or company
    platform = "BSE SME" if "_sme_" in endpoint else "BSE"
    return IpoRecord(
        id=stable_id("bse-performance", source_id, company, listing_date, endpoint),
        company_name=company,
        source="bse",
        source_endpoint=endpoint,
        source_record_id=source_id,
        observed_at=observed_at,
        status="past" if not listing_date or not as_of or date.fromisoformat(listing_date) <= as_of else "upcoming",
        symbol=clean_text(row.get("Company_Short_Name")),
        exchange_platform=platform,
        security_type="SME" if "_sme_" in endpoint else "Equity",
        issue_type="IPO",
        listing_date=listing_date,
        issue_price=parse_float(row.get("IssuePrice")),
        listing_day_open=parse_float(row.get("ListingDayOpen")),
        listing_day_close=parse_float(row.get("ListingDayClose")),
        listing_open_gain=parse_float(row.get("ListingDayGain")),
        listing_day_gain=parse_float(row.get("ListingDayGain")),
        current_price=parse_float(row.get("CurrentPrice")),
        gain_loss=parse_float(row.get("GainLoss")),
        stock_url=clean_text(row.get("IMAGE")),
        raw=row,
    )


def normalize_trendlyne(snapshot: dict[str, Any], as_of: date | None = None) -> list[IpoRecord]:
    meta = snapshot["meta"]
    endpoint = meta["endpoint"]
    observed_at = meta["fetched_at"]
    body = snapshot["body"]
    if not isinstance(body, dict) or body.get("head", {}).get("status") not in {0, "0"}:
        return []
    if endpoint == "upcoming":
        rows = body.get("body") if isinstance(body.get("body"), list) else []
    elif endpoint.startswith("year_"):
        rows = (((body.get("body") or {}).get("table") or {}).get("row_data") or [])
    else:
        rows = []

    normalized: list[IpoRecord] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        record = normalize_trendlyne_row(row, endpoint, observed_at, as_of)
        if record:
            normalized.append(record)
    return normalized


def normalize_trendlyne_row(row: dict[str, Any], endpoint: str, observed_at: str, as_of: date | None = None) -> IpoRecord | None:
    company = clean_text(row.get("company_name"))
    if not company:
        return None
    source_id = clean_text(row.get("ipo_id") or row.get("company_slug_name") or company) or company
    start = parse_date(row.get("bid_open_date") or row.get("bid_start_date"))
    end = parse_date(row.get("bid_close_date") or row.get("bid_end_date"))
    listing_date = parse_date(row.get("listing_date"))
    drhp_date = parse_date(row.get("drhp_filing_date"))
    low = parse_float(row.get("price_range_min"))
    high = parse_float(row.get("price_range_max"))
    if low is None and row.get("issue_price") is not None:
        low = parse_float(row.get("issue_price"))
    if high is None and row.get("issue_price") is not None:
        high = parse_float(row.get("issue_price"))
    platform = clean_text(row.get("exchange_flags")) or "Trendlyne"
    is_sme = bool(row.get("is_sme"))
    documents = trendlyne_documents(row)
    stock_url = clean_text(row.get("stock_page_url"))
    status = normalize_status(None, start, end, as_of)
    if not status and listing_date:
        status = "past" if not as_of or date.fromisoformat(listing_date) <= as_of else "upcoming"
    if not status:
        status = "document" if drhp_date and not start and not end else "upcoming"

    return IpoRecord(
        id=stable_id("trendlyne", source_id, company, start, end, listing_date, endpoint),
        company_name=company,
        source="trendlyne",
        source_endpoint=endpoint,
        source_record_id=source_id,
        observed_at=observed_at,
        status=status,
        symbol=clean_text(row.get("stock_code") or row.get("NSEcode") or row.get("BSEScriptCode")),
        exchange_platform=platform,
        security_type="SME" if is_sme else "Equity",
        issue_type="IPO",
        issue_start_date=start or drhp_date,
        issue_end_date=end,
        listing_date=listing_date,
        price_band_low=low,
        price_band_high=high,
        price_band_text=trendlyne_price_band_text(low, high),
        issue_price=parse_float(row.get("issue_price")),
        issue_size_text=trendlyne_issue_size_text(row.get("issue_size")),
        subscription_times=parse_float(row.get("total_subscription")),
        listing_day_open=parse_float(row.get("listing_open_price")),
        listing_day_close=parse_float(row.get("listing_close_price")),
        listing_open_gain=parse_float(row.get("listing_gainP")),
        listing_day_gain=parse_float(row.get("listing_gainP")),
        current_price=parse_float(row.get("current_price")),
        gain_loss=parse_float(row.get("current_gainP")),
        stock_url=stock_url,
        documents=documents,
        raw=row,
    )


def trendlyne_price_band_text(low: float | None, high: float | None) -> str | None:
    if low is None and high is None:
        return None
    if low == high:
        return f"{low:g}"
    if low is None:
        return f"up to {high:g}"
    if high is None:
        return f"from {low:g}"
    return f"{low:g} to {high:g}"


def trendlyne_issue_size_text(value: Any) -> str | None:
    amount = parse_float(value)
    if amount is None:
        return clean_text(value)
    return f"{amount / 10_000_000:g} crore"


def trendlyne_documents(row: dict[str, Any]) -> list[dict[str, str]]:
    documents = []
    for key, label in (
        ("ipo_drhp_document", "DRHP"),
        ("ipo_rhp_document", "RHP"),
        ("rhp_external_document", "RHP external"),
    ):
        url = clean_url(row.get(key))
        if url:
            documents.append({"type": label, "url": url})
    return dedupe_documents(documents)


def normalize_moneycontrol(snapshot: dict[str, Any], as_of: date | None = None) -> list[IpoRecord]:
    meta = snapshot["meta"]
    endpoint = meta["endpoint"]
    if endpoint == "listed_ipos_index" or not str(endpoint).startswith("listed_ipos_page_"):
        return []
    observed_at = meta["fetched_at"]
    body = snapshot["body"]
    if not isinstance(body, dict) or body.get("success") not in {1, "1", True}:
        return []
    rows = ((body.get("data") or {}).get("listedIpo") or [])
    normalized: list[IpoRecord] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        record = normalize_moneycontrol_row(row, endpoint, observed_at, as_of)
        if record:
            normalized.append(record)
    return normalized


def normalize_moneycontrol_row(row: dict[str, Any], endpoint: str, observed_at: str, as_of: date | None = None) -> IpoRecord | None:
    company = clean_text(row.get("company_name"))
    if not company:
        return None
    listing_date = parse_date(row.get("listing_date"))
    source_id = clean_text("|".join(str(part) for part in (row.get("sc_id"), row.get("company_code"), listing_date) if part)) or company
    status = "past"
    if listing_date and as_of and date.fromisoformat(listing_date) > as_of:
        status = "upcoming"

    return IpoRecord(
        id=stable_id("moneycontrol", source_id, company, listing_date, clean_text(row.get("ipo_type"))),
        company_name=company,
        source="moneycontrol",
        source_endpoint=endpoint,
        source_record_id=source_id,
        observed_at=observed_at,
        status=status,
        symbol=clean_text(row.get("sc_id")),
        exchange_platform="Moneycontrol",
        security_type=clean_text(row.get("ipo_type")),
        issue_type="IPO",
        listing_date=listing_date,
        issue_price=parse_float(row.get("issue_price")),
        issue_size_text=moneycontrol_issue_size_text(row.get("issue_size")),
        subscription_times=parse_float(row.get("total_subs")),
        listing_day_open=parse_float(row.get("dt_open")),
        listing_day_close=parse_float(row.get("dt_close")),
        listing_open_gain=parse_float(row.get("listing_gain")),
        listing_day_gain=parse_float(row.get("listing_gain")),
        current_price=parse_float(row.get("last_price")),
        current_gain_from_listing_open=parse_float(row.get("todays_gain")),
        stock_url=moneycontrol_stock_url(row.get("url")),
        raw=row,
    )


def moneycontrol_issue_size_text(value: Any) -> str | None:
    amount = parse_float(value)
    if amount is None:
        return clean_text(value)
    crores = amount / 10_000_000
    text = f"{crores:.2f}".rstrip("0").rstrip(".")
    return f"{text} crore"


def moneycontrol_stock_url(value: Any) -> str | None:
    path = clean_text(value)
    if not path:
        return None
    if path.startswith("http://") or path.startswith("https://"):
        return clean_url(path)
    return f"https://www.moneycontrol.com/india/stockpricequote/{path.lstrip('/')}"


def normalize_snapshots(snapshots: list[dict[str, Any]], as_of: date | None = None) -> list[dict[str, Any]]:
    records: list[IpoRecord] = []
    active_capitalmarket_pages = capitalmarket_active_pages(snapshots)
    active_moneycontrol_pages = moneycontrol_active_pages(snapshots)
    for snapshot in snapshots:
        source = snapshot.get("meta", {}).get("source")
        if source == "nse":
            records.extend(normalize_nse(snapshot, as_of))
        elif source == "bse":
            records.extend(normalize_bse(snapshot, as_of))
        elif source == "capitalmarket":
            endpoint = snapshot.get("meta", {}).get("endpoint")
            if active_capitalmarket_pages and endpoint not in active_capitalmarket_pages and not str(endpoint).endswith("_index"):
                continue
            from .capitalmarket import parse_capitalmarket_ipo_history

            records.extend(parse_capitalmarket_ipo_history(snapshot, as_of))
        elif source == "trendlyne":
            records.extend(normalize_trendlyne(snapshot, as_of))
        elif source == "moneycontrol":
            endpoint = snapshot.get("meta", {}).get("endpoint")
            if active_moneycontrol_pages and endpoint not in active_moneycontrol_pages and endpoint != "listed_ipos_index":
                continue
            records.extend(normalize_moneycontrol(snapshot, as_of))
    return [record.as_dict() for record in records]


def capitalmarket_active_pages(snapshots: list[dict[str, Any]]) -> set[str]:
    pages: set[str] = set()
    for snapshot in snapshots:
        meta = snapshot.get("meta", {})
        if meta.get("source") != "capitalmarket" or not str(meta.get("endpoint", "")).endswith("_index"):
            continue
        body = snapshot.get("body")
        if not isinstance(body, dict):
            continue
        for page in body.get("pages") or []:
            if isinstance(page, dict) and page.get("endpoint"):
                pages.add(str(page["endpoint"]))
    return pages


def moneycontrol_active_pages(snapshots: list[dict[str, Any]]) -> set[str]:
    pages: set[str] = set()
    for snapshot in snapshots:
        meta = snapshot.get("meta", {})
        if meta.get("source") != "moneycontrol" or meta.get("endpoint") != "listed_ipos_index":
            continue
        body = snapshot.get("body")
        if not isinstance(body, dict):
            continue
        for page in body.get("pages") or []:
            if isinstance(page, dict) and page.get("endpoint"):
                pages.add(str(page["endpoint"]))
    return pages


def merge_for_site(records: list[dict[str, Any]], as_of: date | None = None) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for record in records:
        key = site_merge_key(record)
        existing = groups.get(key)
        if not existing:
            merged = dict(record)
            merged["sources"] = [source_summary(record)]
            merged.pop("raw", None)
            if as_of:
                redact_future_fields(merged, as_of)
            groups[key] = merged
            continue

        existing["sources"].append(source_summary(record))
        for field in (
            "symbol",
            "status",
            "exchange_platform",
            "security_type",
            "issue_type",
            "listing_date",
            "price_band_low",
            "price_band_high",
            "price_band_text",
            "face_value",
            "issue_price",
            "issue_size_text",
            "shares_offered",
            "shares_bid",
            "subscription_times",
            "listing_day_open",
            "listing_day_close",
            "listing_open_gain",
            "listing_day_gain",
            "current_price",
            "current_gain_from_listing_open",
            "gain_loss",
            "stock_url",
        ):
            if existing.get(field) in (None, "") and record.get(field) not in (None, ""):
                existing[field] = record[field]
        existing_docs = {(doc.get("type"), doc.get("url")) for doc in existing.get("documents", [])}
        for doc in record.get("documents", []):
            doc_key = (doc.get("type"), doc.get("url"))
            if doc_key not in existing_docs:
                existing.setdefault("documents", []).append(doc)
                existing_docs.add(doc_key)
        if as_of:
            redact_future_fields(existing, as_of)
    return sorted(groups.values(), key=lambda item: (item.get("issue_start_date") or "9999", item.get("company_name") or ""))


def site_merge_key(record: dict[str, Any]) -> str:
    company = slugify(record.get("company_name") or "")
    issue_type = slugify(record.get("issue_type") or "")
    start = record.get("issue_start_date") or ""
    end = record.get("issue_end_date") or ""
    listing = record.get("listing_date") or ""
    if start or end:
        date_key = f"{start}|{end}"
    elif listing:
        date_key = f"listing|{listing}"
    else:
        date_key = f"undated|{record.get('source_endpoint') or ''}"
    return "|".join([company, issue_type, date_key])


def source_summary(record: dict[str, Any]) -> dict[str, str | None]:
    return {
        "record_id": record.get("id"),
        "source": record.get("source"),
        "endpoint": record.get("source_endpoint"),
        "source_record_id": record.get("source_record_id"),
        "observed_at": record.get("observed_at"),
    }


def redact_future_fields(record: dict[str, Any], as_of: date) -> None:
    redactions = record.setdefault("redactions", [])
    issue_end = parse_date(record.get("issue_end_date"))
    listing = parse_date(record.get("listing_date"))

    if listing and date.fromisoformat(listing) > as_of and record.get("listing_date") is not None:
        record["listing_date"] = None
        redactions.append({"field": "listing_date", "reason": "after_as_of"})
        for field in ("listing_day_open", "listing_day_close", "listing_open_gain", "listing_day_gain", "current_price", "current_gain_from_listing_open", "gain_loss", "stock_url"):
            if record.get(field) is not None:
                record[field] = None
                redactions.append({"field": field, "reason": "listing_after_as_of"})

    if issue_end and date.fromisoformat(issue_end) > as_of:
        for field in ("issue_price",):
            if record.get(field) is not None:
                record[field] = None
                redactions.append({"field": field, "reason": "issue_not_closed_as_of"})

    if not redactions:
        record.pop("redactions", None)
