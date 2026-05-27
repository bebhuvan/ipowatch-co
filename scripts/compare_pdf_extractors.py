"""Compare PDF text/markdown extraction backends on a primary filing.

This is intentionally an experimental harness. It does not publish V3 facts.
It writes raw extractor outputs plus a compact benchmark report so we can
decide whether MarkItDown/LiteParse are good verifier inputs for DRHPs.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from ipo_portal.filing_processor import PROJECT_ROOT as IPO_PROJECT_ROOT
from ipo_portal.filing_processor import download_pdf


DEFAULT_REPORT = IPO_PROJECT_ROOT / "data" / "reports" / "pdf_extractor_benchmark.json"
DEFAULT_OUT_ROOT = IPO_PROJECT_ROOT / "data" / "cache" / "pdf_extraction"
SECTION_KEYWORDS = [
    "OUR BUSINESS",
    "INDUSTRY OVERVIEW",
    "RISK FACTORS",
    "OBJECTS OF THE OFFER",
    "OBJECTS OF THE ISSUE",
    "BASIS FOR OFFER PRICE",
    "FINANCIAL INFORMATION",
    "RESTATED",
    "OUR PROMOTERS",
    "OUTSTANDING LITIGATION",
]


@dataclass(frozen=True)
class ExtractorResult:
    name: str
    ok: bool
    elapsed_ms: int
    chars: int = 0
    lines: int = 0
    output_path: str | None = None
    error: str | None = None
    keyword_hits: dict[str, int] | None = None
    sample: str | None = None
    page_scoped_pdf: str | None = None


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare PDF extraction backends on one filing PDF.")
    parser.add_argument("--url", required=True, help="Direct PDF/ZIP URL. ZIPs are unwrapped to the largest PDF.")
    parser.add_argument("--slug", required=True)
    parser.add_argument("--pages", default="5-50", help="Page range for page-aware extractors, e.g. 5-50. Use all for full PDF.")
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--timeout", type=int, default=240, help="Per-extractor timeout in seconds.")
    parser.add_argument(
        "--extractor",
        action="append",
        choices=["pdftotext", "markitdown", "liteparse"],
        help="Restrict to one extractor. Repeatable. Defaults to all.",
    )
    args = parser.parse_args()

    _, pdf_path = download_pdf(args.url)
    out_dir = args.out_root / args.slug
    out_dir.mkdir(parents=True, exist_ok=True)
    scoped_pdf = make_scoped_pdf(pdf_path, args.pages, out_dir)

    wanted = args.extractor or ["pdftotext", "markitdown", "liteparse"]
    registry: dict[str, Callable[[Path, str, Path], str]] = {
        "pdftotext": extract_pdftotext,
        "markitdown": extract_markitdown,
        "liteparse": extract_liteparse,
    }

    results = []
    for name in wanted:
        input_pdf = pdf_path if name == "pdftotext" else scoped_pdf
        results.append(run_extractor(name, registry[name], input_pdf, args.pages, out_dir, args.timeout))

    report = {
        "slug": args.slug,
        "url": args.url,
        "local_pdf_path": str(pdf_path),
        "page_scoped_pdf_path": str(scoped_pdf) if scoped_pdf != pdf_path else None,
        "pages": args.pages,
        "results": [result.__dict__ for result in results],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if all(result.ok for result in results) else 1


def run_extractor(
    name: str,
    func: Callable[[Path, str, Path], str],
    pdf_path: Path,
    pages: str,
    out_dir: Path,
    timeout: int,
) -> ExtractorResult:
    started = time.monotonic()
    try:
        output_path = out_dir / f"{name}.md"
        text = run_with_timeout(func, pdf_path, pages, out_dir, output_path, timeout)
        output_path.write_text(text, encoding="utf-8")
        return ExtractorResult(
            name=name,
            ok=True,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            chars=len(text),
            lines=text.count("\n") + 1 if text else 0,
            output_path=str(output_path),
            keyword_hits=keyword_hits(text),
            sample=sample_text(text),
            page_scoped_pdf=str(pdf_path),
        )
    except Exception as exc:  # noqa: BLE001 - benchmark reports per-extractor failures
        return ExtractorResult(
            name=name,
            ok=False,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            error=f"{type(exc).__name__}: {exc}",
        )


def extract_pdftotext(pdf_path: Path, pages: str, out_dir: Path) -> str:
    cmd = ["pdftotext", "-layout"]
    start, end = parse_pages(pages)
    if start is not None:
        cmd.extend(["-f", str(start)])
    if end is not None:
        cmd.extend(["-l", str(end)])
    cmd.extend([str(pdf_path), "-"])
    raw = subprocess.check_output(cmd)
    return raw.decode("utf-8", errors="replace")


def extract_markitdown(pdf_path: Path, pages: str, out_dir: Path) -> str:
    from markitdown import MarkItDown

    # MarkItDown does not expose a stable page-range API, so the harness passes
    # a page-scoped temporary PDF when --pages is not "all".
    result = MarkItDown(enable_plugins=False).convert(str(pdf_path))
    return result.text_content


def extract_liteparse(pdf_path: Path, pages: str, out_dir: Path) -> str:
    from liteparse import LiteParse

    result = LiteParse().parse(
        pdf_path,
        ocr_enabled=False,
        target_pages=None,
        precise_bounding_box=False,
        timeout=600,
    )
    page_chunks = []
    for page in result.pages:
        page_chunks.append(f"\n\n<!-- page {page.pageNum} -->\n\n{page.text}")
    return "\n".join(page_chunks).strip() or result.text


def parse_pages(pages: str) -> tuple[int | None, int | None]:
    if pages == "all":
        return None, None
    if "-" in pages:
        left, _, right = pages.partition("-")
        return int(left), int(right)
    page = int(pages)
    return page, page


def make_scoped_pdf(pdf_path: Path, pages: str, out_dir: Path) -> Path:
    start, end = parse_pages(pages)
    if start is None or end is None:
        return pdf_path
    scoped = out_dir / f"pages-{start}-{end}.pdf"
    if scoped.exists():
        return scoped
    subprocess.check_call(
        [
            "qpdf",
            "--empty",
            "--pages",
            str(pdf_path),
            f"{start}-{end}",
            "--",
            str(scoped),
        ]
    )
    return scoped


def run_with_timeout(
    func: Callable[[Path, str, Path], str],
    pdf_path: Path,
    pages: str,
    out_dir: Path,
    output_path: Path,
    timeout: int,
) -> str:
    queue: mp.Queue = mp.Queue(maxsize=1)
    temp_path = output_path.with_suffix(f".{int(time.time() * 1000)}.tmp")
    process = mp.Process(target=worker_main, args=(queue, func, pdf_path, pages, out_dir, temp_path))
    process.start()
    process.join(timeout)
    if process.is_alive():
        process.terminate()
        process.join(5)
        temp_path.unlink(missing_ok=True)
        raise TimeoutError(f"extractor exceeded {timeout}s timeout")
    if queue.empty():
        temp_path.unlink(missing_ok=True)
        raise RuntimeError(f"extractor exited with code {process.exitcode} and no output")
    ok, payload = queue.get()
    if ok:
        text = temp_path.read_text(encoding="utf-8")
        temp_path.unlink(missing_ok=True)
        return text
    temp_path.unlink(missing_ok=True)
    raise RuntimeError(payload)


def worker_main(
    queue: mp.Queue,
    func: Callable[[Path, str, Path], str],
    pdf_path: Path,
    pages: str,
    out_dir: Path,
    temp_path: Path,
) -> None:
    try:
        temp_path.write_text(func(pdf_path, pages, out_dir), encoding="utf-8")
        queue.put((True, str(temp_path)))
    except Exception as exc:  # noqa: BLE001 - marshalled back to parent report
        queue.put((False, f"{type(exc).__name__}: {exc}"))


def keyword_hits(text: str) -> dict[str, int]:
    upper = text.upper()
    return {keyword: upper.count(keyword) for keyword in SECTION_KEYWORDS if keyword in upper}


def sample_text(text: str) -> str:
    compact = "\n".join(line.rstrip() for line in text.splitlines() if line.strip())
    return compact[:1200]


if __name__ == "__main__":
    raise SystemExit(main())
