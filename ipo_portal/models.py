from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class IpoRecord:
    id: str
    company_name: str
    source: str
    source_endpoint: str
    source_record_id: str
    observed_at: str
    status: str | None = None
    symbol: str | None = None
    exchange_platform: str | None = None
    security_type: str | None = None
    issue_type: str | None = None
    issue_start_date: str | None = None
    issue_end_date: str | None = None
    listing_date: str | None = None
    price_band_low: float | None = None
    price_band_high: float | None = None
    price_band_text: str | None = None
    face_value: float | None = None
    issue_price: float | None = None
    issue_size_text: str | None = None
    shares_offered: int | None = None
    shares_bid: int | None = None
    subscription_times: float | None = None
    listing_day_open: float | None = None
    listing_day_close: float | None = None
    listing_open_gain: float | None = None
    listing_day_gain: float | None = None
    current_price: float | None = None
    current_gain_from_listing_open: float | None = None
    gain_loss: float | None = None
    stock_url: str | None = None
    documents: list[dict[str, str]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
