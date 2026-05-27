# Kite Connect — automated price fetch

How IPO Watch pulls listing-day and current prices from Zerodha Kite
Connect, with automated daily login.

## What it does

* Daily **automated login** via TOTP (no manual browser step).
* Downloads the Kite **instrument master**, maps each IPO issue to its
  instrument, fetches **listing-day candles** and **current LTPs**, plus
  benchmark indices for relative performance.
* Stores everything **locally** under `data/private/kite/` (gitignored).
  Kite-derived prices are never written into public site data with the
  access token attached.

## One-time setup

### 1. Credentials in `.env` (never in chat, never committed)

```ini
KITE_API_KEY=…           # from developers.kite.trade
KITE_API_SECRET=…        # from developers.kite.trade
KITE_USER_ID=AB1234      # your Zerodha client ID
KITE_PASSWORD=…          # your kite.zerodha.com password
KITE_TOTP_SECRET=…       # base32 seed from your authenticator
```

> **Security:** these five together are full login to your brokerage.
> `.env` is gitignored — keep it that way. Treat it like your banking
> password. This integration is **read-only market data**, but the
> credentials themselves are not scoped, so guard them accordingly.

### 2. Getting the TOTP secret out of Proton Pass

Your authenticator stores a base32 *seed* behind the QR code you scanned
when enabling 2FA. In Proton Pass:

1. Open the Zerodha / Kite login entry.
2. Find the **2FA / TOTP / Authenticator** field.
3. Reveal it — Proton Pass shows either the **`otpauth://…` URI** or the
   raw **base32 secret**. Either works.
4. Copy that string into `.env` as `KITE_TOTP_SECRET=…`.

If you only have the QR image, re-run Zerodha's "enable external TOTP"
flow to get a fresh seed, and store *that* in both Proton Pass and
`.env`.

The code accepts either form:
* raw base32 (`JBSWY3DPEHPK3PXP`), or
* full URI (`otpauth://totp/Zerodha:AB1234?secret=…&issuer=Zerodha`).

### 3. Register the API app's redirect URL

In the Kite developer console, the app needs a redirect URL set (any
valid URL — the auto-login captures the `request_token` from the
redirect before it's followed, so the URL doesn't need to be reachable).

## Daily use

```bash
# Refresh the token only if it's missing or past the 06:00 IST expiry.
python -m ipo_portal.kite ensure-session

# Full sync (auto-logs-in first, then fetches everything).
python -m ipo_portal.kite sync
```

`sync` calls `ensure_session()` internally, so the cron only needs:

```cron
30 9 * * 1-5  cd /path/to/IPO && .venv/bin/python -m ipo_portal.kite sync >> data/reports/kite.log 2>&1
```

## Token lifecycle

* Kite invalidates **all** access tokens at ~06:00 IST daily.
* `ensure-session` checks `session.json`'s `fetched_at` against the most
  recent 06:00 IST boundary and only re-logs-in when stale — so repeated
  runs in a day are free (no redundant logins).
* The token lives at `data/private/kite/session.json`.

## Commands

| Command | Purpose |
|---|---|
| `auto-login` | Force a fresh TOTP login now. |
| `ensure-session` | Reuse fresh token, else TOTP-login. Cron entrypoint. |
| `login-url` / `exchange-token` | Manual fallback (browser login → paste request_token). |
| `refresh-instruments` | Download the Kite instrument master. |
| `map-issues` | Map IPO issues to Kite instruments. |
| `backfill-listings` | Listing-day candles for mapped issues. |
| `refresh-current` | Current LTPs. |
| `refresh-benchmarks` | Benchmark index candles. |
| `sync` | ensure-session → instruments → map → listings → current → export. |

## Reconstructing the Trendlyne-style aggregates

We dropped Trendlyne's opaque yearly rollups. With Kite we can compute
them ourselves, verified: per-issue `issue_price`, listing-day OHLC, and
current price are all available, so average listing gain / profit-loss
counts / segment breakdowns per year are a straight aggregation over the
v2 records + Kite prices — every figure traceable to a source candle.

## Failure modes

* **`Could not capture request_token`** — API key wrong, app has no
  redirect URL set, or the account doesn't have Kite Connect enabled.
* **`twofa failed`** — TOTP seed wrong or clock skew. Verify the code in
  `.env` matches what your authenticator shows right now.
* **`login failed`** — wrong `KITE_USER_ID` / `KITE_PASSWORD`.
* All credentials read from `.env` only; nothing is logged.
