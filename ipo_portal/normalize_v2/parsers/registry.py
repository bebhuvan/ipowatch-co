"""Parser registry — dispatch raw snapshots to canonical-field extractors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from ..pipeline import Contribution


@dataclass(frozen=True)
class ParserContext:
    """What the pipeline tells a parser about the snapshot being processed."""

    source: str
    endpoint: str
    snapshot_at: str
    snapshot_url: str | None = None


ParserResult = Iterable[Contribution]
ParserFn = Callable[[Any, ParserContext], ParserResult]


@dataclass
class _Registry:
    by_key: dict[tuple[str, str], ParserFn] = field(default_factory=dict)

    def add(self, source: str, endpoint: str, fn: ParserFn) -> None:
        key = (source, endpoint)
        if key in self.by_key:
            raise ValueError(f"Duplicate parser for {key}")
        self.by_key[key] = fn

    def get(self, source: str, endpoint: str) -> ParserFn | None:
        return self.by_key.get((source, endpoint))


PARSERS = _Registry()


def register_parser(source: str, endpoint: str) -> Callable[[ParserFn], ParserFn]:
    """Decorator: register ``fn`` as the parser for ``(source, endpoint)``."""

    def decorator(fn: ParserFn) -> ParserFn:
        PARSERS.add(source, endpoint, fn)
        return fn

    return decorator


def parser_for(source: str, endpoint: str) -> ParserFn | None:
    return PARSERS.get(source, endpoint)
