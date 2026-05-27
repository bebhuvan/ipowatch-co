"""Identity / slug normalization for v2 records.

Implements the identifier policy from ``docs/data/FUTURE_PROOFING.md`` §7
and the company-name dedup mitigation from ``docs/data/EDGE_CASES.md``
``E.ID.002``.

Key concepts
------------
* **stable_join_key** — the cross-source identifier used to merge rows.
  Preference order: ISIN > (PAN + listing_year) > (normalized_name + window).
* **short_id** — first 6 chars of ``sha1(stable_join_key)``. Survives a
  company rename without breaking joins; the slug stem changes but the
  short-id doesn't.
* **slug** — ``<normalized-name>-<short_id>`` (kebab-case). Renames
  preserve short-id and record old slugs in ``aliases[]``.

ISIN regex and PAN regex are validated structurally; semantic correctness
(check digit, ALPHA / NUMERIC bands) is up to the source. We never accept
PAN-only or symbol-only joins (see ``E.ID.003``, ``E.ID.004``).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable


ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")

# Tokens stripped from company names before slugging. Order doesn't matter;
# the regex below removes any occurrence as a whole token.
_SUFFIX_TOKENS = (
    "ltd",
    "limited",
    "pvt",
    "private",
    "plc",
    "corp",
    "corporation",
    "inc",
    "incorporated",
    "co",
    "company",
    "&",
    "and",
    "holdings",
    "industries",
    "lt",
)

# Characters allowed in the name-derived slug stem.
_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")


def is_valid_isin(value: str | None) -> bool:
    """Structural ISIN check after uppercasing input.

    We never reject a value just because it's lowercased upstream — that
    would create false negatives. ``E.ID.003`` rejects structurally bad
    PANs (wrong length, wrong character classes), not case variance.
    """
    if not value:
        return False
    return bool(ISIN_RE.match(value.strip().upper()))


def is_valid_pan(value: str | None) -> bool:
    if not value:
        return False
    return bool(PAN_RE.match(value.strip().upper()))


def normalize_name(raw: str) -> str:
    """Canonical normalized form for company-name comparison.

    Stable under: case, suffix tokens, whitespace, punctuation, NFC vs NFD,
    common Unicode lookalikes via the unidecode-style fallback.

    Examples
    --------
    >>> normalize_name("Kalana Ispat Ltd")
    'kalana ispat'
    >>> normalize_name("Kalana Ispat Limited")
    'kalana ispat'
    >>> normalize_name("Yatra Online Pvt. Ltd.")
    'yatra online'
    """
    if not raw:
        return ""
    # Drop the BSE per-issue "<n>+" row-marker prefix ("1+ZEE MEDIA…") so a
    # marked record matches the same company from a clean feed. Targeted to
    # the exact artifact — legit digit-led names ("7NR", "5paisa") survive.
    raw = re.sub(r"^\s*\d+\+\s*", "", raw)
    text = unicodedata.normalize("NFKD", raw)
    # Strip combining diacritics — "café" → "cafe".
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    # Replace any run of non-alphanumeric with a single space.
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    if not text:
        return ""
    tokens = [tok for tok in text.split() if tok and tok not in _SUFFIX_TOKENS]
    return " ".join(tokens)


def stable_join_key(
    isin: str | None = None,
    pan: str | None = None,
    listing_year: int | None = None,
    normalized_name: str | None = None,
    listing_date_window_iso: str | None = None,
) -> str:
    """Return the canonical join key. Highest-priority discriminator wins.

    Returns a discriminator-prefixed string so callers don't accidentally
    mix ``isin:INE...`` with ``pan:ABC...``. Empty inputs are skipped.
    """
    if is_valid_isin(isin):
        return f"isin:{isin.strip().upper()}"  # type: ignore[union-attr]
    if is_valid_pan(pan) and listing_year:
        return f"pan_year:{pan.strip().upper()}:{listing_year}"  # type: ignore[union-attr]
    if normalized_name and listing_date_window_iso:
        return f"name_date:{normalized_name}:{listing_date_window_iso}"
    if normalized_name and listing_year:
        return f"name_year:{normalized_name}:{listing_year}"
    raise ValueError(
        "Cannot construct stable_join_key: need ISIN OR (PAN + year) OR "
        "(normalized_name + (year or date window))."
    )


def short_id(join_key: str, length: int = 6) -> str:
    return hashlib.sha1(join_key.encode("utf-8")).hexdigest()[:length]


def slugify(normalized: str, key: str) -> str:
    """Build ``<normalized-name>-<short-id>``. Kebab-cased, lowercase only."""
    stem = _NON_SLUG_RE.sub("-", normalized).strip("-")
    if not stem:
        stem = "issue"
    return f"{stem}-{short_id(key)}"


@dataclass(frozen=True)
class Identity:
    """The full canonical identity bundle for a v2 record."""

    stable_join_key: str
    short_id: str
    slug: str
    normalized_name: str
    isin: str | None = None
    pan: str | None = None
    listing_year: int | None = None
    aliases: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "stable_join_key": self.stable_join_key,
            "short_id": self.short_id,
            "slug": self.slug,
            "normalized_name": self.normalized_name,
            "isin": self.isin,
            "pan": self.pan,
            "listing_year": self.listing_year,
            "aliases": list(self.aliases),
        }


def build_identity(
    company_name: str,
    isin: str | None = None,
    pan: str | None = None,
    listing_year: int | None = None,
    listing_date_window_iso: str | None = None,
    aliases: Iterable[str] = (),
) -> Identity:
    normalized = normalize_name(company_name)
    key = stable_join_key(
        isin=isin,
        pan=pan,
        listing_year=listing_year,
        normalized_name=normalized,
        listing_date_window_iso=listing_date_window_iso,
    )
    sid = short_id(key)
    slug = slugify(normalized, key)
    return Identity(
        stable_join_key=key,
        short_id=sid,
        slug=slug,
        normalized_name=normalized,
        isin=(isin.upper() if is_valid_isin(isin) else None),
        pan=(pan.upper() if is_valid_pan(pan) else None),
        listing_year=listing_year,
        aliases=tuple(aliases),
    )
