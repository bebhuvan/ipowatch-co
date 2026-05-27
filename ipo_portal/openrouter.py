"""OpenRouter OpenAI-compatible client for filing extraction."""

from __future__ import annotations

import hashlib
import base64
import json
import os
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import requests

from .deepseek import DeepSeekError, DeepSeekResponse


DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "qwen/qwen3.5-flash-02-23"
JSON_MODE_MAX_TOKENS_DEFAULT = 8192
JSON_MODE_MAX_TOKENS_CEILING = 65536

# OpenRouter prices are per token in the model API, but we log an estimate
# in USD using current public Qwen3.5-Flash page prices: $0.065/M input,
# $0.26/M output. This is telemetry only, not billing authority.
PRICING_USD_PER_MTOK = {
    "qwen/qwen3.5-flash-02-23": {"prompt": 0.065, "completion": 0.26},
    "qwen/qwen3.5-flash-20260224": {"prompt": 0.065, "completion": 0.26},
    "qwen/qwen3.6-flash": {"prompt": 0.1875, "completion": 1.125},
    "z-ai/glm-4.7-flash": {"prompt": 0.06, "completion": 0.40},
    "z-ai/glm-4.7-flash-20260119": {"prompt": 0.06, "completion": 0.40},
    "qwen/qwen3-vl-8b-instruct": {"prompt": 0.08, "completion": 0.50},
    "qwen/qwen3-vl-30b-a3b-instruct": {"prompt": 0.13, "completion": 0.52},
    "qwen/qwen3-vl-235b-a22b-instruct": {"prompt": 0.20, "completion": 0.88},
    "google/gemini-2.5-flash": {"prompt": 0.30, "completion": 2.50},
    "google/gemini-3.1-flash-lite": {"prompt": 0.25, "completion": 1.50},
}


@dataclass
class _UsageRecord:
    timestamp: str
    provider: str
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


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


class OpenRouterClient:
    """Small cached client for OpenRouter chat completions."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        cache_dir: Path | None = None,
        usage_log: Path | None = None,
        timeout: int = 180,
        max_retries: int = 5,
    ) -> None:
        root = _project_root()
        _load_dotenv(root / ".env")

        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not self.api_key:
            raise DeepSeekError("OPENROUTER_API_KEY not set. Add it to .env before running OpenRouter extraction.")
        self.base_url = (base_url or os.environ.get("OPENROUTER_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.cache_dir = cache_dir or (root / "data" / "cache" / "openrouter")
        self.usage_log = usage_log or (root / "data" / "reports" / "openrouter_usage.jsonl")
        self.timeout = timeout
        self.max_retries = max_retries

        self.session = requests.Session()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        site_url = os.environ.get("OPENROUTER_SITE_URL", "").strip()
        app_title = os.environ.get("OPENROUTER_APP_TITLE", "").strip()
        if site_url:
            headers["HTTP-Referer"] = site_url
        if app_title:
            headers["X-Title"] = app_title
        self.session.headers.update(headers)

    def chat(
        self,
        user: str | list[dict[str, Any]],
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
        effective_max = max_tokens
        if response_format == "json_object" and effective_max is None:
            effective_max = JSON_MODE_MAX_TOKENS_DEFAULT

        attempts: list[dict[str, Any]] = []
        while True:
            payload: dict[str, Any] = {
                "model": model,
                "messages": _build_messages(system, user),
                "temperature": temperature,
            }
            if response_format == "json_object":
                payload["response_format"] = {"type": "json_object"}
            if effective_max is not None:
                payload["max_tokens"] = effective_max
            if extra_body:
                payload.update(extra_body)

            cache_key = _cache_key(payload)
            cached_path = self._cache_path(cache_key)
            if cache and cached_path.exists():
                response = self._load_cached(cached_path, cache_key)
                self._log_usage(response, purpose, extra_telemetry)
                return response

            api_payload, elapsed_ms = self._post_with_retry(payload, purpose)
            response, parse_error = self._try_build_response(api_payload, response_format, cache_key, elapsed_ms)
            finish_reason = _finish_reason(api_payload)
            attempts.append({"max_tokens": effective_max, "finish_reason": finish_reason, "parse_error": parse_error})
            if response_format == "json_object" and (finish_reason == "length" or parse_error is not None):
                next_max = (effective_max or JSON_MODE_MAX_TOKENS_DEFAULT) * 2
                if next_max <= JSON_MODE_MAX_TOKENS_CEILING:
                    effective_max = next_max
                    continue
                raise DeepSeekError(f"OpenRouter JSON output failed/truncated for {purpose}: {attempts}")
            if parse_error is not None:
                raise DeepSeekError(parse_error)
            assert response is not None
            if cache:
                self._write_cache(cached_path, api_payload, response_format)
            self._log_usage(response, purpose, {**(extra_telemetry or {}), "attempts": attempts})
            return response

    def chat_with_pdf(
        self,
        *,
        prompt: str,
        pdf_url: str | None = None,
        pdf_path: Path | None = None,
        pdf_filename: str = "filing.pdf",
        system: str | None = None,
        model: str = DEFAULT_MODEL,
        temperature: float = 0.0,
        response_format: Literal["text", "json_object"] = "text",
        max_tokens: int | None = None,
        purpose: str = "unspecified",
        cache: bool = True,
        pdf_engine: Literal["native", "cloudflare-ai", "mistral-ocr"] | None = "native",
        extra_telemetry: dict[str, Any] | None = None,
    ) -> DeepSeekResponse:
        """Send a PDF as a multimodal file part through OpenRouter.

        Prefer ``pdf_url`` for public SEBI/NSE/BSE PDFs. Use ``pdf_path`` for
        ZIP-wrapped or locally cached PDFs. OpenRouter accepts both direct URLs
        and ``data:application/pdf;base64,...`` file payloads.
        """
        if bool(pdf_url) == bool(pdf_path):
            raise ValueError("chat_with_pdf requires exactly one of pdf_url or pdf_path")
        file_data = pdf_url or _pdf_data_url(pdf_path)
        content = [
            {"type": "text", "text": prompt},
            {"type": "file", "file": {"filename": pdf_filename, "file_data": file_data}},
        ]
        extra_body: dict[str, Any] = {}
        if pdf_engine:
            extra_body["plugins"] = [{"id": "file-parser", "pdf": {"engine": pdf_engine}}]
        return self.chat(
            user=content,
            system=system,
            model=model,
            temperature=temperature,
            response_format=response_format,
            max_tokens=max_tokens,
            purpose=purpose,
            cache=cache,
            extra_telemetry={
                **(extra_telemetry or {}),
                "pdf_input": "url" if pdf_url else "base64",
                "pdf_engine": pdf_engine,
            },
            extra_body=extra_body,
        )

    def _post_with_retry(self, payload: dict[str, Any], purpose: str) -> tuple[dict[str, Any], int]:
        url = f"{self.base_url}/chat/completions"
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            started = time.monotonic()
            try:
                response = self.session.post(url, json=payload, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
                _sleep(attempt)
                continue
            elapsed_ms = int((time.monotonic() - started) * 1000)
            if response.status_code == 200:
                return response.json(), elapsed_ms
            if response.status_code in {408, 409, 429, 500, 502, 503, 504}:
                last_error = DeepSeekError(f"OpenRouter {response.status_code} for {purpose}: {response.text[:300]}")
                _sleep(attempt)
                continue
            raise DeepSeekError(f"OpenRouter {response.status_code} for {purpose}: {response.text[:600]}")
        raise DeepSeekError(f"OpenRouter request failed after {self.max_retries} attempts for {purpose}: {last_error}")

    def _try_build_response(
        self,
        api_payload: dict[str, Any],
        response_format: str,
        cache_key: str,
        elapsed_ms: int,
        cached: bool = False,
    ) -> tuple[DeepSeekResponse | None, str | None]:
        choices = api_payload.get("choices") or []
        if not choices:
            return None, f"OpenRouter returned no choices: {api_payload}"
        message = choices[0].get("message") or {}
        content = str(message.get("content") or "")
        parsed = None
        parse_error = None
        if response_format == "json_object":
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as exc:
                parse_error = f"OpenRouter json_object response was not parseable JSON: {exc}; tail={content[-120:]!r}"
        usage = api_payload.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
        model = api_payload.get("model") or "unknown"
        cost = _estimate_cost_usd(model, prompt_tokens, completion_tokens)
        return (
            DeepSeekResponse(
                content=content,
                json_content=parsed,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cached=cached,
                elapsed_ms=elapsed_ms,
                cache_key=cache_key,
                estimated_cost_usd=cost,
            ),
            parse_error,
        )

    def _cache_path(self, cache_key: str) -> Path:
        return self.cache_dir / cache_key[:2] / f"{cache_key}.json"

    def _load_cached(self, path: Path, cache_key: str) -> DeepSeekResponse:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        response, parse_error = self._try_build_response(
            envelope["api_payload"],
            envelope.get("response_format", "text"),
            cache_key,
            elapsed_ms=0,
            cached=True,
        )
        if parse_error is not None:
            raise DeepSeekError(parse_error)
        assert response is not None
        return response

    def _write_cache(self, path: Path, api_payload: dict[str, Any], response_format: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"cached_at": _now_iso(), "provider": "openrouter", "response_format": response_format, "api_payload": api_payload},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    def _log_usage(self, response: DeepSeekResponse, purpose: str, extra: dict[str, Any] | None) -> None:
        self.usage_log.parent.mkdir(parents=True, exist_ok=True)
        record = _UsageRecord(
            timestamp=_now_iso(),
            provider="openrouter",
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


def _build_messages(system: str | None, user: str | list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})
    return messages


def _pdf_data_url(pdf_path: Path | None) -> str:
    if pdf_path is None:
        raise ValueError("pdf_path is required")
    encoded = base64.b64encode(pdf_path.read_bytes()).decode("ascii")
    return f"data:application/pdf;base64,{encoded}"


def _cache_key(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _finish_reason(api_payload: dict[str, Any]) -> str | None:
    choices = api_payload.get("choices") or []
    reason = choices[0].get("finish_reason") if choices else None
    return str(reason) if reason is not None else None


def _estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    pricing = PRICING_USD_PER_MTOK.get(model)
    if not pricing:
        return 0.0
    return (prompt_tokens / 1_000_000) * pricing["prompt"] + (completion_tokens / 1_000_000) * pricing["completion"]


def _sleep(attempt: int) -> None:
    time.sleep(min((1.5 ** attempt) + random.uniform(0, 1), 30.0))


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
