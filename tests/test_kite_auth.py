"""Tests for Kite TOTP auto-login helpers (no network)."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

import pyotp

from ipo_portal import kite_auth


IST = ZoneInfo("Asia/Kolkata")


class TotpTests(unittest.TestCase):
    def test_raw_secret(self) -> None:
        secret = pyotp.random_base32()
        code = kite_auth.current_totp(secret)
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())
        # Matches a fresh pyotp computation for the same secret/window.
        self.assertEqual(code, pyotp.TOTP(secret).now())

    def test_otpauth_uri(self) -> None:
        secret = pyotp.random_base32()
        uri = f"otpauth://totp/Zerodha:AB1234?secret={secret}&issuer=Zerodha"
        self.assertEqual(kite_auth.current_totp(uri), pyotp.TOTP(secret).now())

    def test_secret_with_spaces(self) -> None:
        secret = pyotp.random_base32()
        spaced = " ".join(secret[i : i + 4] for i in range(0, len(secret), 4))
        self.assertEqual(kite_auth.current_totp(spaced), pyotp.TOTP(secret).now())


class SessionFreshnessTests(unittest.TestCase):
    def _write(self, tmp: str, fetched_at: datetime, token: str | None = "tok") -> Path:
        path = Path(tmp) / "session.json"
        path.write_text(json.dumps({"access_token": token, "fetched_at": fetched_at.isoformat()}))
        return path

    def test_missing_file(self) -> None:
        self.assertFalse(kite_auth.session_is_fresh(Path("/tmp/does-not-exist-xyz.json")))

    def test_fresh_token_after_cutoff(self) -> None:
        with TemporaryDirectory() as tmp:
            # Fetched a few minutes ago — always fresh.
            path = self._write(tmp, datetime.now(timezone.utc) - timedelta(minutes=5))
            self.assertTrue(kite_auth.session_is_fresh(path))

    def test_stale_token_two_days_old(self) -> None:
        with TemporaryDirectory() as tmp:
            path = self._write(tmp, datetime.now(timezone.utc) - timedelta(days=2))
            self.assertFalse(kite_auth.session_is_fresh(path))

    def test_missing_access_token(self) -> None:
        with TemporaryDirectory() as tmp:
            path = self._write(tmp, datetime.now(timezone.utc), token=None)
            self.assertFalse(kite_auth.session_is_fresh(path))


if __name__ == "__main__":
    unittest.main()
