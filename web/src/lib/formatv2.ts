// Display formatters for canonical MACHINE UNITS (data/ipo_watch_v3). The #1 source of bugs
// is rendering a *_paise field as rupees (100× off) — always route money through
// formatINR. Kept separate from the v1 format.ts (which works on plain rupees).

/** integer paise (₹×100) → Indian lakh/crore display with digit grouping. */
export function formatINR(paise: number | null | undefined): string {
  if (paise == null) return '—';
  const rupees = paise / 100;
  const grp = (n: number) =>
    n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (Math.abs(rupees) >= 1e7) return `₹${grp(rupees / 1e7)} Cr`;
  if (Math.abs(rupees) >= 1e5) return `₹${grp(rupees / 1e5)} L`;
  return `₹${grp(rupees)}`;
}

/** integer basis points (1% = 100) → signed percent, e.g. 1455 → "+14.55%". */
export const formatBps = (bps?: number | null): string =>
  bps == null ? '—' : `${bps >= 0 ? '+' : ''}${(bps / 100).toFixed(2)}%`;

/** decimal-STRING multiple, e.g. "191.9300" → "191.93×". */
export const formatX = (x?: string | null): string =>
  x == null ? '—' : `${parseFloat(x).toFixed(2)}×`;

/** raw numeric multiple (trajectory `times`) → "11.74×". */
export const formatTimesV2 = (n?: number | null): string =>
  n == null ? '—' : `${n.toFixed(2)}×`;

/** ISO date → e.g. "8 May 2024" (treats date-only as IST/UTC midnight). */
export const formatDate = (iso?: string | null): string => {
  if (!iso) return '—';
  const d = new Date(iso.length <= 10 ? `${iso}T00:00:00Z` : iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
};

/** A price band like "₹300.00 – ₹315.00", a single price, or "—". */
export const formatBand = (lo?: number | null, hi?: number | null): string => {
  if (lo == null && hi == null) return '—';
  if (lo != null && hi != null && lo !== hi) return `${formatINR(lo)} – ${formatINR(hi)}`;
  return formatINR(hi ?? lo);
};
