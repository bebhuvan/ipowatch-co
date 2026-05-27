"""Zerodha Kite Connect automated daily login (TOTP-based).

Kite access tokens expire every morning (~06:00 IST), so a data pipeline
that runs unattended needs to regenerate the token daily. The official
flow requires a browser login; this module automates it using the
account's own credentials + TOTP seed, exactly as the algo-trading
community does for their own accounts.

Security model
--------------
* Credentials (``KITE_USER_ID``, ``KITE_PASSWORD``, ``KITE_TOTP_SECRET``,
  ``KITE_API_KEY``, ``KITE_API_SECRET``) are read from ``.env`` only —
  never passed on the command line, never logged.
* The resulting ``access_token`` is written to
  ``data/private/kite/session.json`` (gitignored, local-only).
* This is read-only market-data use. Treat ``.env`` as you would your
  banking password — anyone with it can log into the brokerage.

Login flow (3 steps + token exchange)
--------------------------------------
1. ``POST /api/login`` {user_id, password}              → request_id
2. ``POST /api/twofa`` {user_id, request_id, totp}      → session cookie
3. ``GET /connect/login?api_key=…`` (follow redirects)  → request_token
4. ``POST /session/token`` {api_key, request_token, checksum} → access_token

``ensure_session`` is the entrypoint the pipeline calls: it reuses a
still-valid token and only re-logs-in when the token is missing or has
aged past the daily expiry.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import requests

from .storage import utc_now, write_json


KITE_WEB_ROOT = "https://kite.zerodha.com"
API_ROOT = "https://api.kite.trade"
DEFAULT_SESSION_PATH = Path("data/private/kite/session.json")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class KiteAuthError(RuntimeError):
    """Raised when automated login fails at any step."""


def _env(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise KiteAuthError(
            f"Missing {name}. Add it to .env (never pass secrets on the CLI)."
        )
    return value


def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        v = v.strip().strip("'\"")
        if k and k not in os.environ:
            os.environ[k] = v


def current_totp(secret: str) -> str:
    """Return the current 6-digit TOTP for the given base32 secret."""
    try:
        import pyotp
    except ImportError as exc:  # pragma: no cover
        raise KiteAuthError("pyotp is required for TOTP login: pip install pyotp") from exc
    # Tolerate the otpauth:// URI form or a raw base32 seed.
    cleaned = secret.strip()
    if cleaned.startswith("otpauth://"):
        return pyotp.parse_uri(cleaned).now()
    # Strip spaces some authenticator apps add when displaying the seed.
    return pyotp.TOTP(cleaned.replace(" ", "")).now()


def auto_login(session_path: Path = DEFAULT_SESSION_PATH) -> dict[str, Any]:
    """Perform the full TOTP login + token exchange. Writes session.json."""
    _load_dotenv()
    api_key = _env("KITE_API_KEY")
    api_secret = _env("KITE_API_SECRET")
    user_id = _env("KITE_USER_ID")
    password = _env("KITE_PASSWORD")
    totp_secret = _env("KITE_TOTP_SECRET")

    http = requests.Session()
    http.headers.update({"User-Agent": USER_AGENT, "X-Kite-Version": "3"})

    # Step 1 — password login → request_id
    r1 = http.post(
        f"{KITE_WEB_ROOT}/api/login",
        data={"user_id": user_id, "password": password},
        timeout=30,
    )
    j1 = _json(r1, "login")
    request_id = (j1.get("data") or {}).get("request_id")
    if not request_id:
        raise KiteAuthError(f"login step returned no request_id: {j1}")

    # Step 2 — TOTP two-factor
    r2 = http.post(
        f"{KITE_WEB_ROOT}/api/twofa",
        data={
            "user_id": user_id,
            "request_id": request_id,
            "twofa_value": current_totp(totp_secret),
            "twofa_type": "totp",
            "skip_session": "",
        },
        timeout=30,
    )
    _json(r2, "twofa")  # raises on error; sets the session cookie

    # Step 3 — connect/login redirect chain → request_token
    request_token = _capture_request_token(http, api_key)

    # Step 4 — exchange for access_token
    data = _exchange_token(api_key, api_secret, request_token)

    session_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "api_key": api_key,
        "access_token": data["access_token"],
        "user_id": data.get("user_id"),
        "user_name": data.get("user_name"),
        "login_time": data.get("login_time"),
        "fetched_at": utc_now().isoformat(),
        "login_method": "totp_auto",
    }
    write_json(session_path, record)
    return record


def _capture_request_token(http: requests.Session, api_key: str) -> str:
    """Follow the connect/login redirects, grabbing request_token en route.

    The final redirect targets the app's registered redirect URL (which
    may be unreachable from here), so we follow hops manually and stop the
    instant a ``request_token=`` appears in a Location header.
    """
    url = f"{KITE_WEB_ROOT}/connect/login?api_key={api_key}&v=3"
    for _ in range(10):
        resp = http.get(url, allow_redirects=False, timeout=30)
        location = resp.headers.get("Location", "")
        if "request_token=" in location:
            qs = parse_qs(urlparse(location).query)
            tokens = qs.get("request_token")
            if tokens:
                return tokens[0]
        if not location:
            # No redirect and no token — inspect body for an embedded token.
            if "request_token=" in resp.text:
                qs = parse_qs(urlparse("?" + resp.text.split("request_token=", 1)[1]).query)
                if qs.get("request_token"):
                    return qs["request_token"][0]
            break
        url = urljoin(url, location)
    raise KiteAuthError(
        "Could not capture request_token from the connect/login redirect. "
        "Check API key, that the app's redirect URL is configured, and that "
        "the account has Kite Connect enabled."
    )


def _exchange_token(api_key: str, api_secret: str, request_token: str) -> dict[str, Any]:
    checksum = hashlib.sha256(
        f"{api_key}{request_token}{api_secret}".encode("utf-8")
    ).hexdigest()
    resp = requests.post(
        f"{API_ROOT}/session/token",
        data={"api_key": api_key, "request_token": request_token, "checksum": checksum},
        headers={"X-Kite-Version": "3"},
        timeout=30,
    )
    payload = _json(resp, "session/token")
    data = payload.get("data") or {}
    if not data.get("access_token"):
        raise KiteAuthError("token exchange succeeded but returned no access_token")
    return data


def _json(resp: requests.Response, step: str) -> dict[str, Any]:
    try:
        payload = resp.json()
    except ValueError as exc:
        raise KiteAuthError(
            f"{step}: non-JSON response ({resp.status_code}): {resp.text[:160]}"
        ) from exc
    if resp.status_code >= 400 or payload.get("status") == "error":
        raise KiteAuthError(
            f"{step} failed ({resp.status_code}): {payload.get('message') or resp.text[:160]}"
        )
    return payload


def session_is_fresh(session_path: Path = DEFAULT_SESSION_PATH) -> bool:
    """True if a token exists and was fetched after the last 06:00 IST cutoff.

    Kite invalidates all access tokens at ~06:00 IST daily. A token fetched
    after the most recent 06:00 IST is still valid; anything older is stale.
    """
    if not session_path.exists():
        return False
    import json

    try:
        record = json.loads(session_path.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(record["fetched_at"])
    except (ValueError, KeyError, OSError):
        return False
    if not record.get("access_token"):
        return False

    from zoneinfo import ZoneInfo

    ist = ZoneInfo("Asia/Kolkata")
    now_ist = datetime.now(ist)
    cutoff = now_ist.replace(hour=6, minute=0, second=0, microsecond=0)
    if now_ist < cutoff:
        # Before today's 06:00 cutoff — the boundary is yesterday's 06:00.
        cutoff = cutoff - timedelta(days=1)
    return fetched_at.astimezone(ist) >= cutoff


def ensure_session(session_path: Path = DEFAULT_SESSION_PATH, force: bool = False) -> dict[str, Any]:
    """Return a valid session, re-logging-in via TOTP only when needed.

    This is the entrypoint the daily pipeline calls — it's free when the
    token is still fresh, and silently refreshes after the daily expiry.
    """
    import json

    if not force and session_is_fresh(session_path):
        return json.loads(session_path.read_text(encoding="utf-8"))
    return auto_login(session_path)
