"""DeepSeek API client for the v2 rebuild orchestrator.

This module is used by ``ipo_portal.orchestrator`` for analytical work
(field cataloguing, schema design, pollution audit, RHP enrichment, parser
code review). It is NOT called at site-build runtime — deterministic JSON
parsing happens in the existing normalize pipeline.

Design choices worth knowing:

* **Disk-cached** by SHA256 of (model + system + user + temperature +
  response_format). Same inputs → same outputs, replayable for free across
  reruns. Cache lives under ``data/cache/deepseek/``.
* **Usage telemetry** appended to ``data/reports/deepseek_usage.jsonl`` so
  every call is auditable (purpose, tokens, model, timestamps).
* **JSON mode** via ``response_format="json_object"`` with parsed payload
  returned alongside raw text — DeepSeek's OpenAI-compatible API supports
  this and we lean on it heavily for structured catalogues.
* **Retries** on 429/5xx with exponential backoff + jitter.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import requests


DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
REASONING_MODEL = "deepseek-v4-pro"

# When response_format="json_object", default ceiling so big catalogues don't
# get silently truncated. We auto-retry with double this when a truncation
# is detected; the cap stops a single hostile prompt running away with cost.
JSON_MODE_MAX_TOKENS_DEFAULT = 8192
# Generous ceiling: tokens are not the constraint, accuracy is. The cap is a
# safety rail to catch runaway prompts (which usually indicate a *different*
# bug), not a cost control.
JSON_MODE_MAX_TOKENS_CEILING = 65536

# DeepSeek pricing as of 2026-05-24 (USD per 1M tokens). Used only for telemetry —
# not authoritative billing. Update when DeepSeek revises pricing.
PRICING_USD_PER_MTOK = {
    "deepseek-chat": {"prompt": 0.14, "completion": 0.28},
    "deepseek-v4-flash": {"prompt": 0.14, "completion": 0.28},
    "deepseek-reasoner": {"prompt": 0.14, "completion": 0.28},
    "deepseek-v4-pro": {"prompt": 0.435, "completion": 0.87},
}


@dataclass(frozen=True)
class DeepSeekResponse:
    """Result envelope returned by ``DeepSeekClient.chat``.

    ``json_content`` is populated only when ``response_format="json_object"``
    was requested and the model returned parseable JSON. ``cached`` is True
    when the response was served from disk without an API call.
    """

    content: str
    json_content: Any | None
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cached: bool
    elapsed_ms: int
    cache_key: str
    estimated_cost_usd: float


@dataclass
class _UsageRecord:
    timestamp: str
    purpose: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    cached: bool
    cache_key: str
    elapsed_ms: int
    extra: dict[str, Any] = field(default_factory=dict)


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader. Does nothing if file is missing.

    Only sets variables that are not already in os.environ so explicit
    process env always wins over file values.
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


class DeepSeekError(RuntimeError):
    """Raised when DeepSeek API requests fail after retries."""


class DeepSeekClient:
    """Thin OpenAI-compatible client for DeepSeek with caching + telemetry."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        cache_dir: Path | None = None,
        usage_log: Path | None = None,
        timeout: int = 120,
        max_retries: int = 5,
        backoff_base: float = 1.5,
    ) -> None:
        root = _project_root()
        _load_dotenv(root / ".env")

        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not self.api_key:
            raise DeepSeekError(
                "DEEPSEEK_API_KEY not set. Add it to .env or export it before "
                "running the orchestrator."
            )
        self.base_url = (base_url or os.environ.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.cache_dir = cache_dir or (root / "data" / "cache" / "deepseek")
        self.usage_log = usage_log or (root / "data" / "reports" / "deepseek_usage.jsonl")
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    # ------------------------------------------------------------------ public

    def chat(
        self,
        user: str,
        system: str | None = None,
        model: str = DEFAULT_MODEL,
        temperature: float = 0.0,
        response_format: Literal["text", "json_object"] = "text",
        max_tokens: int | None = None,
        purpose: str = "unspecified",
        cache: bool = True,
        extra_telemetry: dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> DeepSeekResponse:
        """Send a chat completion request, with disk caching.

        ``purpose`` is recorded in telemetry so we can attribute spend later
        (e.g., ``"catalog:nse:ipo_current_issue"``).

        For ``response_format="json_object"`` the client will auto-recover
        from truncated outputs: if the model's response was cut off (either
        ``finish_reason=length`` or the JSON simply doesn't parse), it
        retries with double the budget up to ``JSON_MODE_MAX_TOKENS_CEILING``.
        We never cache an unparseable response — cache poisoning is worse
        than re-fetching.
        """
        effective_max = max_tokens
        if response_format == "json_object" and effective_max is None:
            effective_max = JSON_MODE_MAX_TOKENS_DEFAULT

        attempts: list[dict[str, Any]] = []
        last_truncation: str | None = None

        while True:
            payload: dict[str, Any] = {
                "model": model,
                "messages": _build_messages(system, user),
                "temperature": temperature,
            }
            if extra_body:
                payload.update(extra_body)
            if response_format == "json_object":
                payload["response_format"] = {"type": "json_object"}
            if effective_max is not None:
                payload["max_tokens"] = effective_max

            cache_key = _cache_key(payload)
            cached_path = self._cache_path(cache_key)
            if cache and cached_path.exists():
                return self._load_cached(cached_path, cache_key, purpose, extra_telemetry)

            api_payload, elapsed_ms = self._post_with_retry(payload, purpose)
            finish_reason = _finish_reason(api_payload)
            response, parse_error = self._try_build_response(
                api_payload, response_format, cache_key, elapsed_ms,
            )
            attempts.append({
                "max_tokens": effective_max,
                "finish_reason": finish_reason,
                "parse_error": parse_error,
            })

            truncated = finish_reason == "length" or parse_error is not None
            if response_format == "json_object" and truncated:
                last_truncation = (
                    f"finish_reason={finish_reason} parse_error={parse_error}"
                )
                next_max = (effective_max or JSON_MODE_MAX_TOKENS_DEFAULT) * 2
                if next_max <= JSON_MODE_MAX_TOKENS_CEILING:
                    effective_max = next_max
                    continue
                raise DeepSeekError(
                    f"DeepSeek JSON output truncated for purpose={purpose} "
                    f"even at max_tokens={effective_max}. attempts={attempts}"
                )

            if parse_error is not None:
                # Text mode parse_error should be impossible, but guard anyway.
                raise DeepSeekError(parse_error)

            if cache:
                self._write_cache(cached_path, api_payload, response_format)
            self._log_usage(
                response,
                purpose,
                _merge_telemetry(extra_telemetry, attempts, last_truncation),
            )
            return response

    # ----------------------------------------------------------------- internal

    def _post_with_retry(self, payload: dict[str, Any], purpose: str) -> tuple[dict[str, Any], int]:
        url = f"{self.base_url}/chat/completions"
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            started = time.monotonic()
            try:
                response = self.session.post(url, json=payload, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
                self._sleep_backoff(attempt)
                continue
            elapsed_ms = int((time.monotonic() - started) * 1000)

            if response.status_code == 200:
                try:
                    return response.json(), elapsed_ms
                except json.JSONDecodeError as exc:
                    raise DeepSeekError(
                        f"DeepSeek returned non-JSON 200 response: {response.text[:200]}"
                    ) from exc

            if response.status_code in {429, 500, 502, 503, 504}:
                last_error = DeepSeekError(
                    f"DeepSeek {response.status_code} (attempt {attempt}/{self.max_retries}) "
                    f"for purpose={purpose}: {response.text[:200]}"
                )
                self._sleep_backoff(attempt)
                continue

            raise DeepSeekError(
                f"DeepSeek {response.status_code} for purpose={purpose}: {response.text[:500]}"
            )

        raise DeepSeekError(
            f"DeepSeek request failed after {self.max_retries} attempts for purpose={purpose}: {last_error}"
        )

    def _sleep_backoff(self, attempt: int) -> None:
        delay = (self.backoff_base ** attempt) + random.uniform(0, 1)
        time.sleep(min(delay, 30.0))

    def _build_response(
        self,
        api_payload: dict[str, Any],
        response_format: str,
        cache_key: str,
        elapsed_ms: int,
        cached: bool,
    ) -> DeepSeekResponse:
        """Build a response, raising on parse failure. Used for cached loads."""
        response, parse_error = self._try_build_response(
            api_payload, response_format, cache_key, elapsed_ms, cached=cached,
        )
        if parse_error is not None:
            raise DeepSeekError(parse_error)
        assert response is not None  # mypy hint: parse_error None => response set
        return response

    def _try_build_response(
        self,
        api_payload: dict[str, Any],
        response_format: str,
        cache_key: str,
        elapsed_ms: int,
        cached: bool = False,
    ) -> tuple[DeepSeekResponse | None, str | None]:
        """Try to assemble a response. Returns (response, parse_error_or_none).

        Non-raising variant so the retry loop can decide whether to escalate
        ``max_tokens`` or surface the error to the caller.
        """
        choices = api_payload.get("choices") or []
        if not choices:
            return None, f"DeepSeek returned no choices: {api_payload}"
        message = choices[0].get("message") or {}
        content = str(message.get("content") or "")

        json_content: Any | None = None
        parse_error: str | None = None
        if response_format == "json_object":
            try:
                json_content = json.loads(content)
            except json.JSONDecodeError as exc:
                parse_error = (
                    f"json_object response was not parseable JSON "
                    f"(content_len={len(content)}, tail={content[-80:]!r}): {exc}"
                )

        usage = api_payload.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
        model = api_payload.get("model") or "unknown"
        cost = _estimate_cost_usd(model, prompt_tokens, completion_tokens)

        response = DeepSeekResponse(
            content=content,
            json_content=json_content,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cached=cached,
            elapsed_ms=elapsed_ms,
            cache_key=cache_key,
            estimated_cost_usd=cost,
        )
        return response, parse_error

    def _cache_path(self, cache_key: str) -> Path:
        return self.cache_dir / cache_key[:2] / f"{cache_key}.json"

    def _load_cached(
        self,
        path: Path,
        cache_key: str,
        purpose: str,
        extra_telemetry: dict[str, Any] | None,
    ) -> DeepSeekResponse:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        api_payload = envelope["api_payload"]
        response_format = envelope.get("response_format", "text")
        response = self._build_response(api_payload, response_format, cache_key, elapsed_ms=0, cached=True)
        self._log_usage(response, purpose, extra_telemetry)
        return response

    def _write_cache(self, path: Path, api_payload: dict[str, Any], response_format: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        envelope = {
            "cached_at": _now_iso(),
            "response_format": response_format,
            "api_payload": api_payload,
        }
        path.write_text(
            json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _log_usage(
        self,
        response: DeepSeekResponse,
        purpose: str,
        extra: dict[str, Any] | None,
    ) -> None:
        self.usage_log.parent.mkdir(parents=True, exist_ok=True)
        record = _UsageRecord(
            timestamp=_now_iso(),
            purpose=purpose,
            model=response.model,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            total_tokens=response.total_tokens,
            estimated_cost_usd=response.estimated_cost_usd,
            cached=response.cached,
            cache_key=response.cache_key,
            elapsed_ms=response.elapsed_ms,
            extra=extra or {},
        )
        with self.usage_log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record.__dict__, ensure_ascii=False) + "\n")


# -------------------------------------------------------------------- helpers


def _build_messages(system: str | None, user: str) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})
    return messages


def _cache_key(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _finish_reason(api_payload: dict[str, Any]) -> str | None:
    """Pull the OpenAI-style finish_reason from a DeepSeek choices envelope.

    ``"stop"`` is the only healthy value for json_object mode. ``"length"``
    means the model ran out of completion budget and the JSON is almost
    certainly truncated.
    """
    choices = api_payload.get("choices") or []
    if not choices:
        return None
    reason = choices[0].get("finish_reason")
    return str(reason) if reason is not None else None


def _merge_telemetry(
    base: dict[str, Any] | None,
    attempts: list[dict[str, Any]],
    last_truncation: str | None,
) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base or {})
    merged.setdefault("attempts", attempts)
    if last_truncation:
        merged.setdefault("recovered_from_truncation", last_truncation)
    return merged


def _estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    pricing = PRICING_USD_PER_MTOK.get(model)
    if not pricing:
        return 0.0
    return (prompt_tokens / 1_000_000) * pricing["prompt"] + (
        completion_tokens / 1_000_000
    ) * pricing["completion"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
