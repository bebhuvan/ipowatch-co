"""Load and apply source-precedence rules.

The rules live at ``docs/data/SOURCE_PRECEDENCE.yaml``. Each rule maps
a canonical field path to an ordered list of sources; the first source
that provides a non-null value for that field wins. The decision is
recorded in the record's ``field_provenance[]`` envelope.

We hand-roll a tiny YAML parser to avoid adding a runtime PyYAML
dependency. The format we accept is intentionally restricted:

* ``key: value`` pairs (string, integer, list-of-strings inline only).
* List items begin with ``- `` and may contain ``key: value`` pairs.
* Comments start with ``#``. Blank lines ignored.

This is enough for ``SOURCE_PRECEDENCE.yaml`` as written. If the file
grows more complex, switch to PyYAML.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_RULES_PATH = Path("docs/data/SOURCE_PRECEDENCE.yaml")

# Conservative tier order — used when no rule matches.
DEFAULT_TIERS = ("nse", "bse", "capitalmarket", "prime", "trendlyne", "moneycontrol", "kite", "rhp_extract")


@dataclass(frozen=True)
class PrecedenceRule:
    field_path: str  # e.g., "pricing.issue_price_paise"; supports trailing ".*" wildcard
    tiers: tuple[str, ...]
    reason: str | None = None
    rule_id: str | None = None

    def matches(self, path: str) -> bool:
        if self.field_path == path:
            return True
        if self.field_path.endswith(".*"):
            prefix = self.field_path[:-2]
            return path == prefix or path.startswith(prefix + ".")
        return False


@dataclass
class PrecedenceRules:
    defaults: dict[str, tuple[str, ...]] = field(default_factory=dict)
    rules: list[PrecedenceRule] = field(default_factory=list)

    def tier_for(self, field_path: str) -> tuple[str, ...]:
        """Return the ordered tier list to apply for a given field path."""
        for rule in self.rules:
            if rule.matches(field_path):
                return rule.tiers
        return DEFAULT_TIERS

    def pick(self, field_path: str, contributions: dict[str, Any]) -> tuple[Any, str | None]:
        """Given ``{source: value}``, pick the highest-priority non-null value.

        Returns ``(value, winning_source)``. If every contribution is None
        / sentinel, returns ``(None, None)``.
        """
        tiers = self.tier_for(field_path)
        for source in tiers:
            if source in contributions and _is_meaningful(contributions[source]):
                return contributions[source], source
        # Fallback: any meaningful value at all.
        for source, value in contributions.items():
            if _is_meaningful(value):
                return value, source
        return None, None


def _is_meaningful(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, (list, dict)) and not value:
        return False
    return True


def load_precedence(path: Path = DEFAULT_RULES_PATH) -> PrecedenceRules:
    if not path.exists():
        return PrecedenceRules()
    return _parse_yaml(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------- mini-YAML

_LIST_ITEM_RE = re.compile(r"^\s*-\s+")
_KEY_VALUE_RE = re.compile(r"^([A-Za-z0-9_./*-]+)\s*:\s*(.*)$")


def _parse_yaml(text: str) -> PrecedenceRules:
    rules = PrecedenceRules()
    section: str | None = None
    current_rule: dict[str, Any] | None = None

    def commit_rule() -> None:
        nonlocal current_rule
        if current_rule is None:
            return
        field_path = current_rule.get("field")
        tiers = current_rule.get("tiers") or ()
        if field_path and tiers:
            rules.rules.append(
                PrecedenceRule(
                    field_path=str(field_path),
                    tiers=tuple(str(t) for t in tiers),
                    reason=current_rule.get("reason"),
                    rule_id=current_rule.get("rule_id"),
                )
            )
        current_rule = None

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        i += 1
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))

        if indent == 0 and stripped.endswith(":"):
            commit_rule()
            section = stripped[:-1].strip()
            continue

        if section == "defaults":
            m = _KEY_VALUE_RE.match(stripped)
            if m:
                key, value = m.group(1), m.group(2).strip()
                rules.defaults[key] = tuple(_parse_inline_list(value))
            continue

        if section == "rules":
            if _LIST_ITEM_RE.match(raw):
                commit_rule()
                inner = _LIST_ITEM_RE.sub("", raw).strip()
                current_rule = {}
                m = _KEY_VALUE_RE.match(inner)
                if m:
                    # The "field: ..." key sits at the same indent as the
                    # later "tiers:" / "reason:" lines. Their indent is
                    # `indent + 2` because YAML lists use the dash's indent
                    # plus two spaces for the key column.
                    key_indent = indent + 2
                    consumed = _set_rule_field(
                        current_rule, m.group(1), m.group(2).strip(),
                        i, lines, key_indent=key_indent,
                    )
                    i += consumed
                continue
            if current_rule is not None:
                m = _KEY_VALUE_RE.match(stripped)
                if m:
                    consumed = _set_rule_field(
                        current_rule, m.group(1), m.group(2).strip(),
                        i, lines, key_indent=indent,
                    )
                    i += consumed
                continue
    commit_rule()
    return rules


def _set_rule_field(
    rule: dict[str, Any],
    key: str,
    raw_value: str,
    line_index: int,
    lines: list[str],
    key_indent: int = 0,
) -> int:
    """Set ``rule[key] = parsed_value``. Returns lines consumed beyond ``line_index``.

    ``key_indent`` is the indent of the line that contained the key. Folded
    block scalars continue while the indent is **strictly greater** than
    ``key_indent`` (per YAML 1.2 §8.1.1.3).
    """
    if raw_value.startswith("[") and raw_value.endswith("]"):
        rule[key] = _parse_inline_list(raw_value)
        return 0
    if raw_value == ">":
        collected: list[str] = []
        consumed = 0
        while line_index + consumed < len(lines):
            line = lines[line_index + consumed]
            if not line.strip():
                consumed += 1
                continue
            line_indent = len(line) - len(line.lstrip(" "))
            if line_indent <= key_indent:
                break
            collected.append(line.strip())
            consumed += 1
        rule[key] = " ".join(collected)
        return consumed
    rule[key] = raw_value.strip().strip('"').strip("'")
    return 0


def _parse_inline_list(value: str) -> list[str]:
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1]
    else:
        inner = value
    return [item.strip().strip('"').strip("'") for item in inner.split(",") if item.strip()]
