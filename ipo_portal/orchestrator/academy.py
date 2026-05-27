"""IPO Watch Academy — long-form content generation pipeline.

This is the parallel cousin of ``schema_design.py``: same orchestrator
pattern, same DeepSeek client, same envelope/storage conventions, but
the output is prose rather than schemas. The pipeline produces fully
sourced, fact-checked, anti-slop-edited articles for the Academy
section of the site.

Pipeline stages (each writes one artifact under
``data/academy/runs/<slug>/``):

1. ``brief``       Human or template-authored. Defines the article's
                   angle, audience, required claims, must-not-say list.
2. ``harvest``     Claude-side WebFetch of primary sources. Cached as
                   JSON under ``data/academy/sources/``. The harvest
                   is not done inside this module — this module reads
                   what's there. See ``docs/data/ACADEMY_HARVEST.md``.
3. ``outline``     ``deepseek-reasoner``, JSON. Sections + beats, every
                   beat carrying source ids from the harvest.
4. ``draft``       ``deepseek-chat``, text. Long-form Markdown, every
                   claim tagged with ``[^source-id]``.
5. ``factcheck``   ``deepseek-reasoner``, JSON. For each tag, verifies
                   the claim against the cited source excerpt.
6. ``edit``        ``deepseek-reasoner``, text. Anti-slop pass using
                   ``docs/decisions/003-academy-anti-slop-spec.md`` as
                   the system prompt.
7. ``visual``      ``deepseek-chat``, JSON. Tables, pull-quotes,
                   sidebars, formulas. Rendered later in Astro.
8. ``render``      Deterministic Python. Combines edited draft +
                   visual brief into ``web/src/content/academy/<slug>.mdx``.

Reruns are idempotent: each stage hashes its input and writes only
when the output would change. Cached DeepSeek responses are reused
under ``data/cache/deepseek/`` (set up by ``DeepSeekClient``).

The brief and the harvest are upstream of any model call. Both are
manual inputs — there is no automatic content sourcing. The pipeline
fails if either is missing; it does not invent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..deepseek import DeepSeekClient, DeepSeekResponse
from ..storage import write_json
from . import PIPELINE_NAME, __version__
from . import academy_prompts as prompts
from .metadata import build_envelope, schema_url, utc_now_iso


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_ACADEMY_ROOT = PROJECT_ROOT / "data" / "academy"
DEFAULT_SOURCES_ROOT = DEFAULT_ACADEMY_ROOT / "sources"
DEFAULT_RUNS_ROOT = DEFAULT_ACADEMY_ROOT / "runs"
DEFAULT_RENDER_ROOT = PROJECT_ROOT / "web" / "src" / "content" / "academy"
DEFAULT_ANTI_SLOP_SPEC_PATH = (
    PROJECT_ROOT / "docs" / "decisions" / "003-academy-anti-slop-spec.md"
)


# --------------------------------------------------------------------- dataclasses


@dataclass(frozen=True)
class SourceDoc:
    """One harvested source from the corpus.

    ``excerpts`` are the verbatim passages extracted from the page. The
    model only ever sees excerpts, never the full HTML. This bounds
    prompt size and keeps the model from inventing context that wasn't
    in the cited extract.
    """

    source_id: str
    title: str
    url: str
    publisher: str
    published_at: str | None
    fetched_at: str
    source_tier: str  # primary | secondary | enrichment
    document_kind: str  # regulation | circular | rulebook | report | filing | news | book
    excerpts: list[str]
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "title": self.title,
            "url": self.url,
            "publisher": self.publisher,
            "published_at": self.published_at,
            "fetched_at": self.fetched_at,
            "source_tier": self.source_tier,
            "document_kind": self.document_kind,
            "excerpts": list(self.excerpts),
            "notes": self.notes,
        }


@dataclass(frozen=True)
class ArticleBrief:
    """The human-authored brief that anchors the pipeline.

    Required fields are minimal. The rest is filled in by the outline
    stage. The brief is what fixes the article's *intent* before any
    model call. Without it, generations drift.
    """

    slug: str
    section: str  # foundations | mechanics | regulation | glossary | timeline
    working_title: str
    audience_one_line: str
    must_answer: list[str]
    must_not_say: list[str]
    angle: str
    length_target_words: int
    source_ids: list[str]  # subset of harvested sources scoped to this article
    notes: str | None = None

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> ArticleBrief:
        return cls(
            slug=str(payload["slug"]),
            section=str(payload["section"]),
            working_title=str(payload["working_title"]),
            audience_one_line=str(payload["audience_one_line"]),
            must_answer=list(payload.get("must_answer") or []),
            must_not_say=list(payload.get("must_not_say") or []),
            angle=str(payload.get("angle", "")),
            length_target_words=int(payload.get("length_target_words", 1800)),
            source_ids=list(payload.get("source_ids") or []),
            notes=payload.get("notes"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "section": self.section,
            "working_title": self.working_title,
            "audience_one_line": self.audience_one_line,
            "must_answer": list(self.must_answer),
            "must_not_say": list(self.must_not_say),
            "angle": self.angle,
            "length_target_words": self.length_target_words,
            "source_ids": list(self.source_ids),
            "notes": self.notes,
        }


@dataclass
class StageResult:
    """Lightweight record of one stage's run, returned by stage functions."""

    stage: str
    artifact_path: Path
    cached: bool
    model: str | None
    tokens_in: int
    tokens_out: int
    cost_usd: float
    notes: list[str] = field(default_factory=list)


# ------------------------------------------------------------------------ paths


def run_dir(slug: str, runs_root: Path = DEFAULT_RUNS_ROOT) -> Path:
    return runs_root / slug


def brief_path(slug: str, runs_root: Path = DEFAULT_RUNS_ROOT) -> Path:
    return run_dir(slug, runs_root) / "brief.json"


def outline_path(slug: str, runs_root: Path = DEFAULT_RUNS_ROOT) -> Path:
    return run_dir(slug, runs_root) / "outline.json"


def draft_path(slug: str, runs_root: Path = DEFAULT_RUNS_ROOT) -> Path:
    return run_dir(slug, runs_root) / "draft.md"


def factcheck_path(slug: str, runs_root: Path = DEFAULT_RUNS_ROOT) -> Path:
    return run_dir(slug, runs_root) / "fact_check.json"


def edited_path(slug: str, runs_root: Path = DEFAULT_RUNS_ROOT) -> Path:
    return run_dir(slug, runs_root) / "edited.md"


def humanized_path(slug: str, runs_root: Path = DEFAULT_RUNS_ROOT) -> Path:
    return run_dir(slug, runs_root) / "humanized.md"


def visual_path(slug: str, runs_root: Path = DEFAULT_RUNS_ROOT) -> Path:
    return run_dir(slug, runs_root) / "visual_brief.json"


def render_path(slug: str, section: str, render_root: Path = DEFAULT_RENDER_ROOT) -> Path:
    return render_root / section / f"{slug}.mdx"


# ----------------------------------------------------------------------- loaders


def load_brief(slug: str, runs_root: Path = DEFAULT_RUNS_ROOT) -> ArticleBrief:
    path = brief_path(slug, runs_root)
    if not path.exists():
        raise FileNotFoundError(
            f"No brief found at {path}. "
            f"Author one and rerun. See docs/data/ACADEMY_HARVEST.md "
            f"for the brief schema."
        )
    return ArticleBrief.from_json(json.loads(path.read_text(encoding="utf-8")))


def load_source(source_id: str, sources_root: Path = DEFAULT_SOURCES_ROOT) -> SourceDoc:
    path = sources_root / f"{source_id}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Source {source_id!r} not found at {path}. "
            f"Run the harvest stage for this source before drafting."
        )
    body = json.loads(path.read_text(encoding="utf-8"))
    return SourceDoc(
        source_id=str(body["source_id"]),
        title=str(body["title"]),
        url=str(body["url"]),
        publisher=str(body["publisher"]),
        published_at=body.get("published_at"),
        fetched_at=str(body["fetched_at"]),
        source_tier=str(body["source_tier"]),
        document_kind=str(body["document_kind"]),
        excerpts=list(body.get("excerpts") or []),
        notes=body.get("notes"),
    )


def load_corpus(brief: ArticleBrief, sources_root: Path = DEFAULT_SOURCES_ROOT) -> list[SourceDoc]:
    missing: list[str] = []
    corpus: list[SourceDoc] = []
    for sid in brief.source_ids:
        try:
            corpus.append(load_source(sid, sources_root))
        except FileNotFoundError:
            missing.append(sid)
    if missing:
        raise FileNotFoundError(
            f"Brief references sources that have not been harvested: "
            f"{missing}. Harvest these into {sources_root} before running "
            f"the outline stage."
        )
    return corpus


def load_anti_slop_spec(path: Path = DEFAULT_ANTI_SLOP_SPEC_PATH) -> str:
    if not path.exists():
        raise FileNotFoundError(
            f"Anti-slop spec not found at {path}. The pipeline refuses "
            f"to run without it."
        )
    return path.read_text(encoding="utf-8")


def corpus_to_prompt_json(corpus: list[SourceDoc]) -> str:
    """Serialize the corpus into compact JSON for prompt embedding.

    Excerpts are kept verbatim. We do not summarise, paraphrase, or
    truncate excerpts here — the integrity of the corpus is the
    integrity of the article.
    """
    return json.dumps(
        [doc.to_dict() for doc in corpus],
        ensure_ascii=False,
        indent=None,
        separators=(",", ":"),
    )


# ------------------------------------------------------------------------ stages


def run_outline(
    slug: str,
    *,
    client: DeepSeekClient | None = None,
    model: str = "deepseek-reasoner",
    runs_root: Path = DEFAULT_RUNS_ROOT,
    sources_root: Path = DEFAULT_SOURCES_ROOT,
    anti_slop_path: Path = DEFAULT_ANTI_SLOP_SPEC_PATH,
) -> StageResult:
    """Stage 3: produce the JSON outline."""
    client = client or DeepSeekClient()
    brief = load_brief(slug, runs_root)
    corpus = load_corpus(brief, sources_root)
    anti_slop = load_anti_slop_spec(anti_slop_path)

    user_prompt = prompts.OUTLINE_USER_TEMPLATE.format(
        brief_json=json.dumps(brief.to_dict(), ensure_ascii=False),
        corpus_json=corpus_to_prompt_json(corpus),
        anti_slop_spec=anti_slop,
    )
    response = client.chat(
        user=user_prompt,
        system=prompts.OUTLINE_SYSTEM,
        model=model,
        temperature=0.0,
        response_format="json_object",
        purpose=f"academy:outline:{slug}",
        extra_telemetry={"slug": slug, "section": brief.section, "source_count": len(corpus)},
    )
    body = response.json_content
    if not isinstance(body, dict):
        raise RuntimeError(f"Outline response was not a JSON object: {response.content[:200]}")

    blocking_gaps = [g for g in (body.get("gaps") or []) if g.get("blocking")]
    notes: list[str] = []
    if blocking_gaps:
        notes.append(
            f"OUTLINE produced {len(blocking_gaps)} blocking gap(s); "
            f"draft stage will refuse until gaps are closed."
        )

    artifact = {
        **build_envelope(
            schema_name="academy/outline.schema",
            schema_version="1.0.0",
            notes="Stage-3 outline produced by deepseek-reasoner.",
        ),
        "slug": slug,
        "outline": body,
        "model": response.model,
        "tokens_in": response.prompt_tokens,
        "tokens_out": response.completion_tokens,
        "cached": response.cached,
        "estimated_cost_usd": response.estimated_cost_usd,
    }
    out_path = outline_path(slug, runs_root)
    write_json(out_path, artifact)
    return _stage_result("outline", out_path, response, notes)


def run_draft(
    slug: str,
    *,
    client: DeepSeekClient | None = None,
    model: str = "deepseek-chat",
    runs_root: Path = DEFAULT_RUNS_ROOT,
    sources_root: Path = DEFAULT_SOURCES_ROOT,
    anti_slop_path: Path = DEFAULT_ANTI_SLOP_SPEC_PATH,
    refuse_on_blocking_gaps: bool = True,
) -> StageResult:
    """Stage 4: write the long-form Markdown draft."""
    client = client or DeepSeekClient()
    brief = load_brief(slug, runs_root)
    corpus = load_corpus(brief, sources_root)
    anti_slop = load_anti_slop_spec(anti_slop_path)

    outline_doc = _read_json(outline_path(slug, runs_root))
    outline = outline_doc.get("outline") or {}
    blocking_gaps = [g for g in (outline.get("gaps") or []) if g.get("blocking")]
    if blocking_gaps and refuse_on_blocking_gaps:
        raise RuntimeError(
            f"Outline has {len(blocking_gaps)} blocking gap(s) for {slug}. "
            f"Close gaps in the harvest, regenerate the outline, then rerun draft. "
            f"Override with --allow-gaps if you really mean it."
        )

    user_prompt = prompts.DRAFT_USER_TEMPLATE.format(
        outline_json=json.dumps(outline, ensure_ascii=False),
        corpus_json=corpus_to_prompt_json(corpus),
        anti_slop_spec=anti_slop,
    )
    response = client.chat(
        user=user_prompt,
        system=prompts.DRAFT_SYSTEM,
        model=model,
        temperature=0.2,
        response_format="text",
        purpose=f"academy:draft:{slug}",
        extra_telemetry={"slug": slug, "section": brief.section, "source_count": len(corpus)},
    )
    out_path = draft_path(slug, runs_root)
    _write_markdown_artifact(out_path, response.content, slug=slug, stage="draft")
    return _stage_result("draft", out_path, response, notes=[])


def run_factcheck(
    slug: str,
    *,
    client: DeepSeekClient | None = None,
    model: str = "deepseek-reasoner",
    runs_root: Path = DEFAULT_RUNS_ROOT,
    sources_root: Path = DEFAULT_SOURCES_ROOT,
) -> StageResult:
    """Stage 5: verify every tagged claim against the corpus."""
    client = client or DeepSeekClient()
    brief = load_brief(slug, runs_root)
    corpus = load_corpus(brief, sources_root)
    draft_md = _strip_frontmatter(_read_text(draft_path(slug, runs_root)))

    user_prompt = prompts.FACTCHECK_USER_TEMPLATE.format(
        draft_md=draft_md,
        corpus_json=corpus_to_prompt_json(corpus),
    )
    response = client.chat(
        user=user_prompt,
        system=prompts.FACTCHECK_SYSTEM,
        model=model,
        temperature=0.0,
        response_format="json_object",
        purpose=f"academy:factcheck:{slug}",
        extra_telemetry={"slug": slug, "source_count": len(corpus)},
    )
    body = response.json_content
    if not isinstance(body, dict):
        raise RuntimeError(f"Fact-check response was not a JSON object: {response.content[:200]}")

    blocking = int(body.get("blocking_count") or 0)
    notes: list[str] = []
    if blocking:
        notes.append(f"FACTCHECK flagged {blocking} blocking issue(s); resolve before edit pass.")

    artifact = {
        **build_envelope(
            schema_name="academy/factcheck.schema",
            schema_version="1.0.0",
            notes="Stage-5 fact-check produced by deepseek-reasoner.",
        ),
        "slug": slug,
        "report": body,
        "model": response.model,
        "tokens_in": response.prompt_tokens,
        "tokens_out": response.completion_tokens,
        "cached": response.cached,
        "estimated_cost_usd": response.estimated_cost_usd,
    }
    out_path = factcheck_path(slug, runs_root)
    write_json(out_path, artifact)
    return _stage_result("factcheck", out_path, response, notes)


def run_edit(
    slug: str,
    *,
    client: DeepSeekClient | None = None,
    model: str = "deepseek-reasoner",
    runs_root: Path = DEFAULT_RUNS_ROOT,
    anti_slop_path: Path = DEFAULT_ANTI_SLOP_SPEC_PATH,
    refuse_on_blocking_factcheck: bool = True,
) -> StageResult:
    """Stage 6: anti-slop edit pass."""
    client = client or DeepSeekClient()
    anti_slop = load_anti_slop_spec(anti_slop_path)
    draft_md = _strip_frontmatter(_read_text(draft_path(slug, runs_root)))

    fc_doc = _read_json(factcheck_path(slug, runs_root))
    fc_report = fc_doc.get("report") or {}
    blocking = int(fc_report.get("blocking_count") or 0)
    if blocking and refuse_on_blocking_factcheck:
        raise RuntimeError(
            f"Fact-check flagged {blocking} blocking issue(s) on {slug}. "
            f"Resolve them in the draft (or in the harvest) before editing. "
            f"Override with --allow-blocking if you really mean it."
        )

    user_prompt = prompts.EDIT_USER_TEMPLATE.format(
        anti_slop_spec=anti_slop,
        draft_md=draft_md,
    )
    response = client.chat(
        user=user_prompt,
        system=prompts.EDIT_SYSTEM,
        model=model,
        temperature=0.0,
        response_format="text",
        purpose=f"academy:edit:{slug}",
        extra_telemetry={"slug": slug},
    )
    out_path = edited_path(slug, runs_root)
    _write_markdown_artifact(out_path, response.content, slug=slug, stage="edited")
    return _stage_result("edit", out_path, response, notes=[])


def run_humanize(
    slug: str,
    *,
    client: DeepSeekClient | None = None,
    model: str = "deepseek-chat",
    temperature: float = 0.9,
    runs_root: Path = DEFAULT_RUNS_ROOT,
    source_stage: str = "edited",
) -> StageResult:
    """Optional stage: rewrite to lower AI-detector signal.

    Reads the edited draft and rewrites it to break the even statistical
    rhythm detectors key on (uniform sentence length, parallelism,
    predictable word choice, affectless polish), while hard-preserving
    every number, date, name, citation tag, and table cell. Runs at high
    temperature for entropy.

    This does NOT run in the default pipeline. It is invoked explicitly
    (``--from humanize``) because it is an experimental, iterative,
    re-test-against-a-detector loop, and because high-temperature
    rewriting must be re-fact-checked before publication.
    """
    client = client or DeepSeekClient()
    src = edited_path(slug, runs_root) if source_stage == "edited" else draft_path(slug, runs_root)
    draft_md = _strip_frontmatter(_read_text(src))

    user_prompt = prompts.HUMANIZE_USER_TEMPLATE.format(draft_md=draft_md)
    response = client.chat(
        user=user_prompt,
        system=prompts.HUMANIZE_SYSTEM,
        model=model,
        temperature=temperature,
        response_format="text",
        purpose=f"academy:humanize:{slug}",
        extra_telemetry={"slug": slug, "temperature": temperature, "source_stage": source_stage},
    )
    out_path = humanized_path(slug, runs_root)
    _write_markdown_artifact(out_path, response.content, slug=slug, stage="humanized")
    notes = [
        "HUMANIZE is high-temperature: re-run fact-check against this output "
        "before publishing. Verify no number/date/name/citation drifted."
    ]
    return _stage_result("humanize", out_path, response, notes)


def run_visual(
    slug: str,
    *,
    client: DeepSeekClient | None = None,
    model: str = "deepseek-chat",
    runs_root: Path = DEFAULT_RUNS_ROOT,
) -> StageResult:
    """Stage 7: visual brief for the renderer."""
    client = client or DeepSeekClient()
    edited_md = _strip_frontmatter(_read_text(edited_path(slug, runs_root)))

    user_prompt = prompts.VISUAL_USER_TEMPLATE.format(draft_md=edited_md)
    response = client.chat(
        user=user_prompt,
        system=prompts.VISUAL_SYSTEM,
        model=model,
        temperature=0.1,
        response_format="json_object",
        purpose=f"academy:visual:{slug}",
        extra_telemetry={"slug": slug},
    )
    body = response.json_content
    if not isinstance(body, dict):
        raise RuntimeError(f"Visual response was not a JSON object: {response.content[:200]}")

    artifact = {
        **build_envelope(
            schema_name="academy/visual_brief.schema",
            schema_version="1.0.0",
            notes="Stage-7 visual brief produced by deepseek-chat.",
        ),
        "slug": slug,
        "visual_brief": body,
        "model": response.model,
        "tokens_in": response.prompt_tokens,
        "tokens_out": response.completion_tokens,
        "cached": response.cached,
        "estimated_cost_usd": response.estimated_cost_usd,
    }
    out_path = visual_path(slug, runs_root)
    write_json(out_path, artifact)
    return _stage_result("visual", out_path, response, notes=[])


# ----------------------------------------------------------------------- helpers


def _read_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(
            f"Expected artifact missing at {path}. Run the prior stage first."
        )
    return path.read_text(encoding="utf-8")


def _strip_frontmatter(content: str) -> str:
    """Remove a leading YAML frontmatter block, if present.

    Stage artifacts get a `---`-fenced provenance header from
    ``_write_markdown_artifact``. When the next stage reads that file
    and passes the contents to a model, the model often echoes the
    frontmatter back into its output. We strip the frontmatter before
    handing the body to the model so it has nothing to echo.
    """
    if not content.startswith("---\n"):
        return content
    end = content.find("\n---\n", 4)
    if end == -1:
        return content
    return content[end + 5 :].lstrip("\n")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"Expected artifact missing at {path}. Run the prior stage first."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _write_markdown_artifact(path: Path, content: str, *, slug: str, stage: str) -> None:
    """Write a Markdown artifact with a small provenance header.

    The header is a YAML-fenced frontmatter block so downstream tools
    can read provenance without parsing prose. The body below is the
    raw Markdown returned by the model.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "---\n"
        f"slug: {slug}\n"
        f"stage: {stage}\n"
        f"generated_at: {utc_now_iso()}\n"
        f"generated_by: {PIPELINE_NAME}/{__version__}\n"
        "---\n\n"
    )
    path.write_text(header + content.rstrip() + "\n", encoding="utf-8")


def _stage_result(
    stage: str,
    path: Path,
    response: DeepSeekResponse,
    notes: list[str],
) -> StageResult:
    return StageResult(
        stage=stage,
        artifact_path=path,
        cached=response.cached,
        model=response.model,
        tokens_in=response.prompt_tokens,
        tokens_out=response.completion_tokens,
        cost_usd=response.estimated_cost_usd,
        notes=list(notes),
    )


# ----------------------------------------------------------------------- pipeline

PIPELINE_STAGES = ("outline", "draft", "factcheck", "edit", "visual")


def run_pipeline(
    slug: str,
    *,
    from_stage: str = "outline",
    to_stage: str = "visual",
    runs_root: Path = DEFAULT_RUNS_ROOT,
    sources_root: Path = DEFAULT_SOURCES_ROOT,
    anti_slop_path: Path = DEFAULT_ANTI_SLOP_SPEC_PATH,
    allow_gaps: bool = False,
    allow_blocking: bool = False,
    client: DeepSeekClient | None = None,
) -> list[StageResult]:
    """Run a contiguous range of stages for one article.

    The default runs the whole pipeline from outline through visual.
    Pause-and-resume is supported by setting ``from_stage`` and
    ``to_stage``: for example, after a manual draft edit, run
    ``from_stage='factcheck', to_stage='visual'`` to re-verify and
    re-edit without redrafting.
    """
    if from_stage not in PIPELINE_STAGES:
        raise ValueError(f"Unknown from_stage {from_stage!r}; valid: {PIPELINE_STAGES}")
    if to_stage not in PIPELINE_STAGES:
        raise ValueError(f"Unknown to_stage {to_stage!r}; valid: {PIPELINE_STAGES}")
    start = PIPELINE_STAGES.index(from_stage)
    stop = PIPELINE_STAGES.index(to_stage)
    if stop < start:
        raise ValueError("to_stage must be at or after from_stage")
    client = client or DeepSeekClient()

    results: list[StageResult] = []
    for stage in PIPELINE_STAGES[start : stop + 1]:
        if stage == "outline":
            results.append(run_outline(slug, client=client, runs_root=runs_root, sources_root=sources_root, anti_slop_path=anti_slop_path))
        elif stage == "draft":
            results.append(run_draft(slug, client=client, runs_root=runs_root, sources_root=sources_root, anti_slop_path=anti_slop_path, refuse_on_blocking_gaps=not allow_gaps))
        elif stage == "factcheck":
            results.append(run_factcheck(slug, client=client, runs_root=runs_root, sources_root=sources_root))
        elif stage == "edit":
            results.append(run_edit(slug, client=client, runs_root=runs_root, anti_slop_path=anti_slop_path, refuse_on_blocking_factcheck=not allow_blocking))
        elif stage == "visual":
            results.append(run_visual(slug, client=client, runs_root=runs_root))
    return results
