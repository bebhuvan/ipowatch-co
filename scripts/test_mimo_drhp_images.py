#!/usr/bin/env python3
"""Render DRHP/RHP pages to JPEG and test MiMo visual extraction.

This is an experimental benchmark harness. It does not write public V3 facts.
It writes rendered JPEG cache files and a report under data/reports/.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from ipo_portal.filing_processor import SECTION_SPECS, extract_pdf_text, slice_for_spec  # noqa: E402


DEFAULT_SITE_ROOT = PROJECT_ROOT / "data" / "ipo_watch_v3"
IMAGE_CACHE_ROOT = PROJECT_ROOT / "data" / "cache" / "pdf_images" / "jpeg"
REPORT_PATH = PROJECT_ROOT / "data" / "reports" / "mimo_drhp_image_test.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", default="a-g-universal-c13e20", help="V3 issue slug.")
    parser.add_argument("--section", default="financials", help="Section spec name to locate.")
    parser.add_argument("--pages", type=int, default=2, help="Number of pages to send from the located section.")
    parser.add_argument("--dpi", type=int, default=180, help="JPEG render DPI.")
    parser.add_argument("--max-tokens", type=int, default=8192, help="Maximum completion tokens for each MiMo call.")
    parser.add_argument(
        "--models",
        default=None,
        help="Comma-separated MiMo model list. Defaults to MIMO_COMPARE_MODELS or mimo-v2.5,mimo-v2-omni.",
    )
    parser.add_argument("--site-root", type=Path, default=DEFAULT_SITE_ROOT)
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.environ.get("MIMO_API_KEY")
    if not api_key:
        raise SystemExit("MIMO_API_KEY is not set in .env")
    base_url = (os.environ.get("MIMO_BASE_URL") or "https://api.xiaomimimo.com/v1").rstrip("/")
    models = parse_models(args.models or os.environ.get("MIMO_COMPARE_MODELS") or "mimo-v2.5,mimo-v2-omni")
    if not models:
        raise SystemExit("No MiMo models configured")

    prospectus = read_prospectus(args.site_root, args.slug)
    pdf_path = Path(prospectus["local_pdf_path"])
    if not pdf_path.exists():
        raise SystemExit(f"Cached PDF not found: {pdf_path}")

    spec = next((item for item in SECTION_SPECS if item["name"] == args.section), None)
    if spec is None:
        known = ", ".join(item["name"] for item in SECTION_SPECS)
        raise SystemExit(f"Unknown section {args.section!r}; known sections: {known}")

    pdf = extract_pdf_text(pdf_path)
    _, page_start, page_end, method = slice_for_spec(pdf, spec)
    if method == "not_found":
        raise SystemExit(f"Could not locate section {args.section!r} in {args.slug}")
    selected_pages = list(range(page_start, min(page_end, page_start + args.pages - 1) + 1))
    image_paths = [render_page_jpeg(pdf_path, args.slug, page, args.dpi) for page in selected_pages]

    prompt = prompt_for_section(args.slug, args.section, selected_pages)
    results = []
    for model in models:
        started = time.monotonic()
        row: dict[str, Any] = {"model": model, "ok": False}
        try:
            response = call_mimo(
                base_url=base_url,
                api_key=api_key,
                model=model,
                prompt=prompt,
                image_paths=image_paths,
                max_tokens=args.max_tokens,
            )
            content = response_content(response)
            parsed = parse_json_content(content)
            usage = response.get("usage") if isinstance(response, dict) else None
            row.update(
                {
                    "ok": parsed is not None,
                    "latency_ms": int((time.monotonic() - started) * 1000),
                    "content_preview": content[:1200],
                    "json": parsed,
                    "usage": usage,
                }
            )
        except Exception as exc:  # noqa: BLE001 - benchmark reports model failures
            row.update(
                {
                    "ok": False,
                    "latency_ms": int((time.monotonic() - started) * 1000),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        results.append(row)

    report = {
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "slug": args.slug,
        "section": args.section,
        "section_locator": {"method": method, "page_start": page_start, "page_end": page_end},
        "sent_pages": selected_pages,
        "dpi": args.dpi,
        "images": [str(path.relative_to(PROJECT_ROOT)) for path in image_paths],
        "models": results,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summarize(report), ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if any(row.get("ok") for row in results) else 1


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def parse_models(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def read_prospectus(site_root: Path, slug: str) -> dict[str, Any]:
    path = site_root / "issues" / slug / "prospectus_facts.json"
    if not path.exists():
        raise SystemExit(f"No prospectus_facts.json for slug {slug}: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def render_page_jpeg(pdf_path: Path, slug: str, page: int, dpi: int) -> Path:
    out_dir = IMAGE_CACHE_ROOT / slug / f"dpi-{dpi}"
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = out_dir / f"page-{page:04d}"
    expected = Path(f"{prefix}-{page}.jpg")
    if expected.exists():
        return expected
    for old in out_dir.glob(f"page-{page:04d}-*.jpg"):
        old.unlink()
    subprocess.check_call(
        [
            "pdftoppm",
            "-f",
            str(page),
            "-l",
            str(page),
            "-jpeg",
            "-r",
            str(dpi),
            str(pdf_path),
            str(prefix),
        ]
    )
    rendered = sorted(out_dir.glob(f"page-{page:04d}-*.jpg"))
    if not rendered:
        raise RuntimeError(f"pdftoppm did not render page {page}")
    return rendered[0]


def prompt_for_section(slug: str, section: str, pages: list[int]) -> str:
    return (
        "You are testing visual extraction from Indian DRHP/RHP filing page images. "
        "Return JSON only. Use only the supplied JPEG page images. Do not use outside knowledge. "
        "Extract visible table/fact content relevant to the requested section. "
        "Return at most 8 facts and at most 2 table previews. "
        "Every extracted item must include value, source_page, raw_excerpt, and confidence. "
        "raw_excerpt must be a short exact visible text fragment from the image.\n\n"
        f"Issue slug: {slug}\n"
        f"Requested section: {section}\n"
        f"PDF pages supplied: {pages}\n\n"
        "Return shape:\n"
        "{"
        "\"section\": string, "
        "\"pages\": [integer], "
        "\"facts\": [{\"label\": string, \"value\": string|null, \"source_page\": integer, "
        "\"raw_excerpt\": string, \"confidence\": \"high|medium|low\"}], "
        "\"tables_detected\": [{\"title\": string|null, \"source_page\": integer, \"columns\": [string], "
        "\"rows_preview\": [[string]]}], "
        "\"notes\": [string]"
        "}"
    )


def call_mimo(
    *,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    image_paths: list[Path],
    max_tokens: int,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    for path in image_paths:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}})
    content.append({"type": "text", "text": prompt})
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are MiMo, a precise visual document extraction assistant. Return JSON only.",
            },
            {"role": "user", "content": content},
        ],
        "max_completion_tokens": max_tokens,
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=data,
        headers={"api-key": api_key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"MiMo HTTP {exc.code}: {body[:1000]}") from exc


def response_content(response: dict[str, Any]) -> str:
    choices = response.get("choices") if isinstance(response, dict) else None
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    return json.dumps(content, ensure_ascii=False)


def parse_json_content(content: str) -> Any | None:
    if not content:
        return None
    content = content.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        content = "\n".join(lines).strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(content[start : end + 1])
            except json.JSONDecodeError:
                return None
    return None


def summarize(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "report": str(REPORT_PATH.relative_to(PROJECT_ROOT)),
        "slug": report["slug"],
        "section": report["section"],
        "sent_pages": report["sent_pages"],
        "images": report["images"],
        "models": [
            {
                "model": row.get("model"),
                "ok": row.get("ok"),
                "latency_ms": row.get("latency_ms"),
                "facts": len(((row.get("json") or {}).get("facts") or [])) if isinstance(row.get("json"), dict) else 0,
                "error": row.get("error"),
                "usage": row.get("usage"),
            }
            for row in report["models"]
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())
