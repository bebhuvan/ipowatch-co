from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")


def write_json(path: Path, payload: Any) -> bool:
    """Write payload as JSON, but skip if existing file content is byte-identical.

    Returns True if the file was (re)written, False if skipped. Hash-gating keeps
    hourly builds from rewriting thousands of unchanged files and prevents the
    git repo from accruing no-op diffs.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    new_body = json_bytes(payload) + b"\n"
    if path.exists():
        try:
            existing_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            new_hash = hashlib.sha256(new_body).hexdigest()
            if existing_hash == new_hash:
                return False
        except OSError:
            pass
    path.write_bytes(new_body)
    return True


def save_raw_snapshot(
    root: Path,
    source: str,
    endpoint_name: str,
    url: str,
    body: Any,
    fetched_at: datetime | None = None,
    status_code: int | None = None,
    elapsed_ms: int | None = None,
) -> Path:
    fetched_at = fetched_at or utc_now()
    body_hash = hashlib.sha256(json_bytes(body)).hexdigest()
    payload = {
        "meta": {
            "source": source,
            "endpoint": endpoint_name,
            "url": url,
            "fetched_at": fetched_at.isoformat(),
            "status_code": status_code,
            "elapsed_ms": elapsed_ms,
            "body_sha256": body_hash,
        },
        "body": body,
    }
    stamp = fetched_at.strftime("%Y%m%dT%H%M%SZ")
    path = root / "raw" / source / endpoint_name / f"{stamp}.json"
    write_json(path, payload)
    return path


def append_source_event(root: Path, event: dict[str, Any]) -> Path:
    """Append a machine-readable source event without changing latest raw data."""
    path = root / "reports" / "source_fetch_events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "recorded_at": utc_now().isoformat(),
        **event,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def latest_snapshot(root: Path, source: str, endpoint_name: str) -> dict[str, Any] | None:
    folder = root / "raw" / source / endpoint_name
    files = sorted(folder.glob("*.json"))
    if not files:
        return None
    return json.loads(files[-1].read_text(encoding="utf-8"))


def load_latest_snapshots(root: Path) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    raw_root = root / "raw"
    if not raw_root.exists():
        return snapshots
    for file_path in sorted(raw_root.glob("*/*/*.json")):
        endpoint_dir = file_path.parent
        if file_path != sorted(endpoint_dir.glob("*.json"))[-1]:
            continue
        snapshots.append(json.loads(file_path.read_text(encoding="utf-8")))
    return snapshots
