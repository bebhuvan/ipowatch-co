"""Phase 6: extract structured prospectus data from RHP / DRHP PDFs.

Goal: for every issue with an RHP / DRHP document URL in our v2 record,
download the PDF, send the extracted text to DeepSeek, and pull out
fields the exchange feeds don't surface as first-class data:

* Anchor investors (name, shares allotted, amount)
* Lead managers / book-running lead managers (BRLM)
* Registrar (RTA)
* Lot size / minimum bid
* Objects of the issue (use of proceeds breakdown)
* Risk factor categories (count and one-line summary)
* Promoter holding pre / post issue
* Listing timeline as stated in the prospectus
* Reservation breakdown (employee, shareholder, policyholder)

Output: ``data/site_v2/issues/{slug}/prospectus.json`` with a metadata
envelope. The extraction is provenance-tagged as
``confidence: "enrichment"`` so primary exchange feeds always win where
they overlap (see ``SOURCE_PRECEDENCE.yaml``).

Implementation notes
--------------------
* PDF text extraction uses ``pdfplumber`` if available, falling back to
  ``pdfminer.six`` or a subprocess call to ``pdftotext`` (poppler). All
  three are optional — extraction errors raise ``RHPExtractionError`` so
  the issue's v2 record gets a quality finding instead of a partial body.
* Each PDF is hashed; results are cached under
  ``data/cache/rhp_extract/<sha256>.json`` so a re-run is free.
* Cost guard: DeepSeek calls are billed per prospectus; the orchestrator
  CLI accepts ``--limit`` so a backfill can be staged.

This module currently builds the framework. The DeepSeek-extraction
prompt is finalized after Phase 2 schemas land so the output JSON shape
matches the canonical schema's nested objects.
"""

from __future__ import annotations

import hashlib
import json
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Protocol

import requests

from ..deepseek import DeepSeekClient
from ..http import HttpClient
from ..storage import write_json
from . import PIPELINE_NAME, __version__
from .metadata import SourceRef, build_envelope, utc_now_iso


DEFAULT_PDF_CACHE = Path("data/cache/rhp_pdfs")
DEFAULT_EXTRACT_CACHE = Path("data/cache/rhp_extract")
DEFAULT_OUTPUT_ROOT = Path("data/site_v2/issues")


class RHPExtractionError(RuntimeError):
    """Raised when PDF download or text extraction fails."""


class PDFTextExtractor(Protocol):
    """Plug-in interface for PDF -> text extraction."""

    def extract(self, pdf_bytes: bytes) -> str: ...


def _try_import_extractor() -> PDFTextExtractor | None:
    """Best-effort PDF backend discovery (pdfplumber, pdfminer, pdftotext)."""
    try:
        import pdfplumber  # type: ignore
    except ImportError:
        pdfplumber = None
    if pdfplumber is not None:
        class _Plumber:
            def extract(self, pdf_bytes: bytes) -> str:
                from io import BytesIO
                text_parts: list[str] = []
                with pdfplumber.open(BytesIO(pdf_bytes)) as doc:
                    for page in doc.pages:
                        page_text = page.extract_text() or ""
                        text_parts.append(page_text)
                return "\n\n".join(text_parts)
        return _Plumber()

    try:
        from pdfminer.high_level import extract_text  # type: ignore
    except ImportError:
        extract_text = None  # type: ignore
    if extract_text is not None:
        class _Miner:
            def extract(self, pdf_bytes: bytes) -> str:
                from io import BytesIO
                return extract_text(BytesIO(pdf_bytes))  # type: ignore[arg-type]
        return _Miner()

    # Fallback: shell out to `pdftotext` if installed.
    import shutil
    import subprocess
    if shutil.which("pdftotext"):
        class _PopplerCli:
            def extract(self, pdf_bytes: bytes) -> str:
                proc = subprocess.run(
                    ["pdftotext", "-layout", "-", "-"],
                    input=pdf_bytes,
                    capture_output=True,
                    check=True,
                )
                return proc.stdout.decode("utf-8", errors="replace")
        return _PopplerCli()
    return None


# ----------------------------------------------------------------- model


@dataclass
class RHPSource:
    """One PDF URL to extract from."""

    issue_slug: str
    document_kind: str  # "drhp" | "rhp" | "prospectus" | "abridged_prospectus"
    url: str
    source: str  # "nse" or "bse"
    endpoint: str  # the document index endpoint we found it on
    snapshot_at: str


@dataclass
class RHPExtraction:
    issue_slug: str
    url: str
    pdf_sha256: str
    extracted_at: str
    text_chars: int
    fields: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


# ----------------------------------------------------------------- runner


@dataclass
class RHPPipeline:
    """Orchestrator for downloading + extracting RHP data."""

    http: HttpClient = field(default_factory=HttpClient)
    deepseek: DeepSeekClient = field(default_factory=DeepSeekClient)
    pdf_cache: Path = DEFAULT_PDF_CACHE
    extract_cache: Path = DEFAULT_EXTRACT_CACHE
    output_root: Path = DEFAULT_OUTPUT_ROOT
    extractor: PDFTextExtractor | None = None

    def __post_init__(self) -> None:
        if self.extractor is None:
            self.extractor = _try_import_extractor()

    def fetch_pdf(self, url: str) -> bytes:
        """Download a PDF, with disk caching keyed by URL hash."""
        url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()
        cached = self.pdf_cache / url_hash[:2] / f"{url_hash}.pdf"
        if cached.exists():
            return cached.read_bytes()
        try:
            response = requests.get(url, timeout=60, headers={"User-Agent": "ipo-watch/1.0"})
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RHPExtractionError(f"PDF download failed for {url}: {exc}") from exc
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(response.content)
        return response.content

    def extract_text(self, pdf_bytes: bytes) -> str:
        if self.extractor is None:
            raise RHPExtractionError(
                "No PDF backend installed. Install pdfplumber or pdfminer.six, "
                "or ensure `pdftotext` (poppler) is on PATH."
            )
        try:
            return self.extractor.extract(pdf_bytes)
        except Exception as exc:  # noqa: BLE001 — backend may raise anything
            raise RHPExtractionError(f"PDF text extraction failed: {exc!r}") from exc

    def extract_one(self, source: RHPSource) -> RHPExtraction:
        pdf_bytes = self.fetch_pdf(source.url)
        pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()
        cached_extract = self.extract_cache / pdf_hash[:2] / f"{pdf_hash}.json"
        if cached_extract.exists():
            payload = json.loads(cached_extract.read_text(encoding="utf-8"))
            return RHPExtraction(
                issue_slug=source.issue_slug,
                url=source.url,
                pdf_sha256=pdf_hash,
                extracted_at=payload.get("extracted_at", ""),
                text_chars=payload.get("text_chars", 0),
                fields=payload.get("fields", {}),
                warnings=payload.get("warnings", []),
            )

        text = self.extract_text(pdf_bytes)
        prompt = _build_extraction_prompt(text, source)
        response = self.deepseek.chat(
            user=prompt,
            system=_EXTRACTION_SYSTEM,
            response_format="json_object",
            purpose=f"rhp_extract:{source.issue_slug}",
            extra_telemetry={
                "document_kind": source.document_kind,
                "text_chars": len(text),
                "pdf_sha256": pdf_hash,
            },
        )
        body = response.json_content if isinstance(response.json_content, dict) else {}
        extraction = RHPExtraction(
            issue_slug=source.issue_slug,
            url=source.url,
            pdf_sha256=pdf_hash,
            extracted_at=utc_now_iso(),
            text_chars=len(text),
            fields=body.get("fields", {}),
            warnings=body.get("warnings", []),
        )
        cached_extract.parent.mkdir(parents=True, exist_ok=True)
        write_json(cached_extract, extraction.__dict__)
        return extraction

    def persist(self, extraction: RHPExtraction, source: RHPSource) -> Path:
        """Write the extraction to ``data/site_v2/issues/{slug}/prospectus.json``."""
        envelope = build_envelope(
            schema_name="prospectus.schema",
            schema_version="1.0.0",
            sources=[
                SourceRef(
                    source=source.source,
                    endpoint=source.endpoint,
                    snapshot_at=source.snapshot_at,
                    url=source.url,
                    confidence="enrichment",
                )
            ],
            notes=(
                "DeepSeek-extracted fields from the RHP / DRHP. "
                "Tier: enrichment — primary exchange feeds win where they overlap."
            ),
            extra={
                "schema_url_self": f"data/site_v2/issues/{source.issue_slug}/prospectus.json",
            },
        )
        document = {
            **envelope,
            "issue_slug": source.issue_slug,
            "document_kind": source.document_kind,
            "document_url": source.url,
            "pdf_sha256": extraction.pdf_sha256,
            "extracted_at": extraction.extracted_at,
            "text_chars": extraction.text_chars,
            "fields": extraction.fields,
            "warnings": extraction.warnings,
        }
        out_path = self.output_root / source.issue_slug / "prospectus.json"
        write_json(out_path, document)
        return out_path


def run_rhp_enrich(
    sources: Iterable[RHPSource],
    pipeline: RHPPipeline | None = None,
    limit: int | None = None,
) -> list[Path]:
    pipeline = pipeline or RHPPipeline()
    results: list[Path] = []
    for i, source in enumerate(sources):
        if limit is not None and i >= limit:
            break
        extraction = pipeline.extract_one(source)
        results.append(pipeline.persist(extraction, source))
    return results


# ----------------------------------------------------------------- prompts

_EXTRACTION_SYSTEM = """You are an SEC-style prospectus analyst extracting structured data from an Indian Red Herring Prospectus (RHP) / Draft RHP for downstream ingestion.

Output STRICT JSON only. Never fabricate. If a field is not stated in the document, return null and add a note in `warnings[]`. Currency values are stored as integer **paise** (₹ × 100). Dates as ISO 8601.

Be conservative: prefer null over plausible-sounding guesses. The extracted JSON will be machine-merged with exchange-feed data, so wrong values are worse than missing values."""


def _build_extraction_prompt(text: str, source: RHPSource) -> str:
    """Build the per-prospectus user prompt. Truncates text to a budget.

    The text budget is intentionally generous because the canonical RHP
    extract spans 200+ pages; we'd rather pay for the prompt than miss
    fields. ``MAX_TEXT_CHARS`` can be tuned per source if a prospectus
    has very dense critical front-matter (where the data we want lives).
    """
    MAX_TEXT_CHARS = 240_000  # ~60k tokens
    truncated = len(text) > MAX_TEXT_CHARS
    if truncated:
        # Keep the front-matter heavy section + the bookrunner pages near
        # the end. We split 80/20 front/back so anchor and BRLM tables
        # near the end of the document aren't lost.
        front = text[: int(MAX_TEXT_CHARS * 0.8)]
        back = text[-int(MAX_TEXT_CHARS * 0.2):]
        text = f"{front}\n\n... [MIDDLE TRUNCATED] ...\n\n{back}"

    parsed_url = urllib.parse.urlparse(source.url)
    return f"""Extract structured fields from the following RHP-class document.

DOCUMENT METADATA:
issue_slug: {source.issue_slug}
document_kind: {source.document_kind}
document_url: {source.url}
document_filename: {Path(parsed_url.path).name}

REQUIRED OUTPUT SHAPE:
{{
  "fields": {{
    "lead_managers": [{{"name": "...", "role": "BRLM | book runner | lead manager"}}],
    "registrar": {{"name": "...", "address": "...", "email": "...", "phone": "..."}},
    "lot_size": <integer shares>,
    "minimum_bid_amount_paise": <integer or null>,
    "anchor_investors": [{{"name": "...", "shares_allotted": <int>, "amount_paise": <int or null>}}],
    "objects_of_issue": [{{"category": "...", "amount_paise": <int>, "description": "..."}}],
    "promoter_holding": {{"pre_issue_pct_bps": <int basis points>, "post_issue_pct_bps": <int>}},
    "reservation": {{"employee_shares": <int or null>, "shareholder_shares": <int or null>, "policyholder_shares": <int or null>}},
    "listing_timeline": {{"open_date": "...", "close_date": "...", "allotment_date": "...", "refund_date": "...", "listing_date": "..."}},
    "risk_factors_summary": {{"category_count": <int>, "top_categories": ["...", "..."]}},
    "issue_size": {{"fresh_issue_paise": <int>, "ofs_paise": <int>, "total_paise": <int>}}
  }},
  "warnings": ["<freeform alerts where extraction was uncertain or document is malformed>"]
}}

DOCUMENT TEXT{' (truncated to 240k chars)' if truncated else ''}:
{text}
"""
