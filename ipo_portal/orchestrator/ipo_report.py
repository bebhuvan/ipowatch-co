"""IPO research-report generation pipeline.

Parallel cousin of ``academy.py``. Where academy writes evergreen
explainers from a harvested web corpus, this writes a per-company IPO
research report from that company's filing, using the page-cited,
citation-validated facts in ``prospectus_facts.json`` as the corpus.

Stages (each writes one artifact under ``data/ipo_reports/runs/<slug>/``):

1. ``adapt``      Deterministic. ``ipo_report_adapter`` turns the issue's
                  facts + bundles into brief.json, facts_digest.json,
                  corpus.json. Runs once at the head of the pipeline.
2. ``outline``    deepseek-reasoner, JSON. Report structure, beats cite pages.
3. ``draft``      deepseek-chat, Markdown. Synthesised prose, ``[^drhp-pNN]``.
4. ``factcheck``  deepseek-reasoner, JSON. Verifies every citation. GATE.
5. ``edit``       deepseek-reasoner, Markdown. Anti-slop pass. GATE.
6. ``visual``     deepseek-chat, JSON. Tables / pull-quotes / sidebars.
7. ``render``     Deterministic. Merges edited draft + visual brief into
                  ``web/src/content/ipo-reports/<slug>.mdx`` with frontmatter
                  and resolved footnote citations.

Reruns are idempotent at the DeepSeek layer (response cache). Pause and
resume with ``from_stage`` / ``to_stage``.

Run one report:
    python -m ipo_portal.orchestrator.ipo_report <issue-slug>
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..deepseek import DeepSeekClient
from ..storage import write_json
from . import PIPELINE_NAME, __version__
from . import ipo_report_prompts as prompts
from .academy import (
    SourceDoc,
    StageResult,
    _read_json,
    _read_text,
    _stage_result,
    _strip_frontmatter,
    _write_markdown_artifact,
    corpus_to_prompt_json,
    load_anti_slop_spec,
    DEFAULT_ANTI_SLOP_SPEC_PATH,
)
from .ipo_report_adapter import build_report_inputs
from .metadata import build_envelope, utc_now_iso


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_RUNS_ROOT = PROJECT_ROOT / "data" / "ipo_reports" / "runs"
DEFAULT_RENDER_ROOT = PROJECT_ROOT / "web" / "src" / "content" / "ipo-reports"


# --------------------------------------------------------------------- paths


def run_dir(slug: str, runs_root: Path = DEFAULT_RUNS_ROOT) -> Path:
    return runs_root / slug


def brief_path(slug: str, runs_root: Path = DEFAULT_RUNS_ROOT) -> Path:
    return run_dir(slug, runs_root) / "brief.json"


def facts_path(slug: str, runs_root: Path = DEFAULT_RUNS_ROOT) -> Path:
    return run_dir(slug, runs_root) / "facts_digest.json"


def corpus_path(slug: str, runs_root: Path = DEFAULT_RUNS_ROOT) -> Path:
    return run_dir(slug, runs_root) / "corpus.json"


def outline_path(slug: str, runs_root: Path = DEFAULT_RUNS_ROOT) -> Path:
    return run_dir(slug, runs_root) / "outline.json"


def draft_path(slug: str, runs_root: Path = DEFAULT_RUNS_ROOT) -> Path:
    return run_dir(slug, runs_root) / "draft.md"


def factcheck_path(slug: str, runs_root: Path = DEFAULT_RUNS_ROOT) -> Path:
    return run_dir(slug, runs_root) / "fact_check.json"


def edited_path(slug: str, runs_root: Path = DEFAULT_RUNS_ROOT) -> Path:
    return run_dir(slug, runs_root) / "edited.md"


def visual_path(slug: str, runs_root: Path = DEFAULT_RUNS_ROOT) -> Path:
    return run_dir(slug, runs_root) / "visual_brief.json"


def render_path(slug: str, render_root: Path = DEFAULT_RENDER_ROOT) -> Path:
    # Markdown, not MDX: GFM gives us footnotes + tables, and we avoid MDX's
    # JSX parsing choking on prose like "<₹10 lakhs" or stray braces.
    return render_root / f"{slug}.md"


# --------------------------------------------------------------------- loaders


def _corpus_from_disk(slug: str, runs_root: Path = DEFAULT_RUNS_ROOT) -> list[SourceDoc]:
    body = _read_json(corpus_path(slug, runs_root))
    return [
        SourceDoc(
            source_id=str(d["source_id"]),
            title=str(d["title"]),
            url=str(d["url"]),
            publisher=str(d["publisher"]),
            published_at=d.get("published_at"),
            fetched_at=str(d.get("fetched_at") or utc_now_iso()),
            source_tier=str(d.get("source_tier", "primary")),
            document_kind=str(d.get("document_kind", "filing")),
            excerpts=list(d.get("excerpts") or []),
            notes=d.get("notes"),
        )
        for d in (body.get("corpus") or [])
    ]


def _sources_index_json(corpus: list[SourceDoc]) -> str:
    """Light source map for outline/draft: ids + pages, no excerpts."""
    return json.dumps(
        [{"source_id": d.source_id, "title": d.title} for d in corpus],
        ensure_ascii=False,
        separators=(",", ":"),
    )


# --------------------------------------------------------------------- adapt stage


def run_adapt(
    issue_slug: str,
    *,
    runs_root: Path = DEFAULT_RUNS_ROOT,
    issues_root: Path | None = None,
) -> dict[str, Any]:
    """Stage 1: build brief + facts digest + corpus, persist to the run dir."""
    kwargs: dict[str, Any] = {}
    if issues_root is not None:
        kwargs["issues_root"] = issues_root
    inputs = build_report_inputs(issue_slug, **kwargs)

    write_json(brief_path(issue_slug, runs_root), inputs.brief.to_dict())
    write_json(
        facts_path(issue_slug, runs_root),
        {"slug": issue_slug, "facts_digest": inputs.facts_digest},
    )
    write_json(
        corpus_path(issue_slug, runs_root),
        {
            "slug": issue_slug,
            "documents": inputs.documents,
            "corpus": [d.to_dict() for d in inputs.corpus],
        },
    )
    return {
        "brief": inputs.brief,
        "facts_digest": inputs.facts_digest,
        "corpus": inputs.corpus,
        "documents": inputs.documents,
    }


# --------------------------------------------------------------------- model stages


def run_outline(
    slug: str,
    *,
    client: DeepSeekClient | None = None,
    model: str = "deepseek-reasoner",
    runs_root: Path = DEFAULT_RUNS_ROOT,
    anti_slop_path: Path = DEFAULT_ANTI_SLOP_SPEC_PATH,
) -> StageResult:
    client = client or DeepSeekClient()
    brief = _read_json(brief_path(slug, runs_root))
    facts = _read_json(facts_path(slug, runs_root)).get("facts_digest") or {}
    corpus = _corpus_from_disk(slug, runs_root)
    anti_slop = load_anti_slop_spec(anti_slop_path)

    user_prompt = prompts.OUTLINE_USER_TEMPLATE.format(
        brief_json=json.dumps(brief, ensure_ascii=False),
        facts_json=json.dumps(facts, ensure_ascii=False, separators=(",", ":")),
        sources_json=_sources_index_json(corpus),
        anti_slop_spec=anti_slop,
    )
    response = client.chat(
        user=user_prompt,
        system=prompts.OUTLINE_SYSTEM,
        model=model,
        temperature=0.0,
        response_format="json_object",
        purpose=f"ipo_report:outline:{slug}",
        extra_telemetry={"slug": slug, "source_count": len(corpus)},
    )
    body = response.json_content
    if not isinstance(body, dict):
        raise RuntimeError(f"Outline response was not JSON: {response.content[:200]}")

    blocking_gaps = [g for g in (body.get("gaps") or []) if g.get("blocking")]
    notes = (
        [f"OUTLINE produced {len(blocking_gaps)} blocking gap(s); draft will refuse."]
        if blocking_gaps
        else []
    )
    write_json(
        outline_path(slug, runs_root),
        {
            **build_envelope("ipo-report/outline.schema", "1.0.0", notes="Stage-2 outline."),
            "slug": slug,
            "outline": body,
            "model": response.model,
            "estimated_cost_usd": response.estimated_cost_usd,
        },
    )
    return _stage_result("outline", outline_path(slug, runs_root), response, notes)


def run_draft(
    slug: str,
    *,
    client: DeepSeekClient | None = None,
    model: str = "deepseek-chat",
    runs_root: Path = DEFAULT_RUNS_ROOT,
    anti_slop_path: Path = DEFAULT_ANTI_SLOP_SPEC_PATH,
    refuse_on_blocking_gaps: bool = True,
) -> StageResult:
    client = client or DeepSeekClient()
    brief = _read_json(brief_path(slug, runs_root))
    facts = _read_json(facts_path(slug, runs_root)).get("facts_digest") or {}
    corpus = _corpus_from_disk(slug, runs_root)
    anti_slop = load_anti_slop_spec(anti_slop_path)

    outline = (_read_json(outline_path(slug, runs_root)).get("outline")) or {}
    blocking_gaps = [g for g in (outline.get("gaps") or []) if g.get("blocking")]
    if blocking_gaps and refuse_on_blocking_gaps:
        raise RuntimeError(
            f"Outline has {len(blocking_gaps)} blocking gap(s) for {slug}. "
            f"Override with allow_gaps=True if intended."
        )

    user_prompt = prompts.DRAFT_USER_TEMPLATE.format(
        outline_json=json.dumps(outline, ensure_ascii=False),
        facts_json=json.dumps(facts, ensure_ascii=False, separators=(",", ":")),
        sources_json=_sources_index_json(corpus),
        anti_slop_spec=anti_slop,
        length_target_words=brief.get("length_target_words", 1900),
    )
    response = client.chat(
        user=user_prompt,
        system=prompts.DRAFT_SYSTEM,
        model=model,
        temperature=0.2,
        response_format="text",
        purpose=f"ipo_report:draft:{slug}",
        extra_telemetry={"slug": slug, "source_count": len(corpus)},
    )
    _write_markdown_artifact(draft_path(slug, runs_root), response.content, slug=slug, stage="draft")
    return _stage_result("draft", draft_path(slug, runs_root), response, notes=[])


def run_factcheck(
    slug: str,
    *,
    client: DeepSeekClient | None = None,
    model: str = "deepseek-reasoner",
    runs_root: Path = DEFAULT_RUNS_ROOT,
) -> StageResult:
    client = client or DeepSeekClient()
    corpus = _corpus_from_disk(slug, runs_root)
    draft_md = _strip_frontmatter(_read_text(draft_path(slug, runs_root)))

    user_prompt = prompts.FACTCHECK_USER_TEMPLATE.format(
        draft_md=draft_md,
        sources_json=corpus_to_prompt_json(corpus),
    )
    response = client.chat(
        user=user_prompt,
        system=prompts.FACTCHECK_SYSTEM,
        model=model,
        temperature=0.0,
        response_format="json_object",
        purpose=f"ipo_report:factcheck:{slug}",
        extra_telemetry={"slug": slug, "source_count": len(corpus)},
    )
    body = response.json_content
    if not isinstance(body, dict):
        raise RuntimeError(f"Fact-check response was not JSON: {response.content[:200]}")
    blocking = int(body.get("blocking_count") or 0)
    notes = [f"FACTCHECK flagged {blocking} blocking issue(s)."] if blocking else []
    write_json(
        factcheck_path(slug, runs_root),
        {
            **build_envelope("ipo-report/factcheck.schema", "1.0.0", notes="Stage-4 fact-check."),
            "slug": slug,
            "report": body,
            "model": response.model,
            "estimated_cost_usd": response.estimated_cost_usd,
        },
    )
    return _stage_result("factcheck", factcheck_path(slug, runs_root), response, notes)


def _blocking_findings(fc_report: dict[str, Any]) -> dict[str, Any]:
    """The subset of the fact-check the repair pass must act on."""
    actionable = {"unsupported", "wrong-source", "partial"}
    checks = [c for c in (fc_report.get("checks") or []) if c.get("verdict") in actionable]
    untagged = [
        c
        for c in (fc_report.get("untagged_claims") or [])
        if c.get("kind") in {"number", "date", "name", "segment", "rule"}
    ]
    return {"checks": checks, "untagged_claims": untagged}


def run_repair(
    slug: str,
    *,
    client: DeepSeekClient | None = None,
    model: str = "deepseek-reasoner",
    runs_root: Path = DEFAULT_RUNS_ROOT,
) -> StageResult:
    """Re-ground the claims the fact-check flagged, rewriting draft.md in place."""
    client = client or DeepSeekClient()
    corpus = _corpus_from_disk(slug, runs_root)
    draft_md = _strip_frontmatter(_read_text(draft_path(slug, runs_root)))
    fc = (_read_json(factcheck_path(slug, runs_root)).get("report")) or {}
    findings = _blocking_findings(fc)

    user_prompt = prompts.REPAIR_USER_TEMPLATE.format(
        draft_md=draft_md,
        findings_json=json.dumps(findings, ensure_ascii=False, separators=(",", ":")),
        sources_json=corpus_to_prompt_json(corpus),
    )
    response = client.chat(
        user=user_prompt,
        system=prompts.REPAIR_SYSTEM,
        model=model,
        temperature=0.0,
        response_format="text",
        purpose=f"ipo_report:repair:{slug}",
        extra_telemetry={"slug": slug, "findings": len(findings["checks"]) + len(findings["untagged_claims"])},
    )
    _write_markdown_artifact(draft_path(slug, runs_root), response.content, slug=slug, stage="draft")
    notes = [f"REPAIR rewrote {len(findings['checks']) + len(findings['untagged_claims'])} flagged claim(s)."]
    return _stage_result("repair", draft_path(slug, runs_root), response, notes)


def run_edit(
    slug: str,
    *,
    client: DeepSeekClient | None = None,
    model: str = "deepseek-reasoner",
    runs_root: Path = DEFAULT_RUNS_ROOT,
    anti_slop_path: Path = DEFAULT_ANTI_SLOP_SPEC_PATH,
    refuse_on_blocking_factcheck: bool = True,
) -> StageResult:
    client = client or DeepSeekClient()
    anti_slop = load_anti_slop_spec(anti_slop_path)
    draft_md = _strip_frontmatter(_read_text(draft_path(slug, runs_root)))

    fc = (_read_json(factcheck_path(slug, runs_root)).get("report")) or {}
    blocking = int(fc.get("blocking_count") or 0)
    if blocking and refuse_on_blocking_factcheck:
        raise RuntimeError(
            f"Fact-check flagged {blocking} blocking issue(s) on {slug}. "
            f"Resolve them, or override with allow_blocking=True."
        )

    user_prompt = prompts.EDIT_USER_TEMPLATE.format(anti_slop_spec=anti_slop, draft_md=draft_md)
    response = client.chat(
        user=user_prompt,
        system=prompts.EDIT_SYSTEM,
        model=model,
        temperature=0.0,
        response_format="text",
        purpose=f"ipo_report:edit:{slug}",
        extra_telemetry={"slug": slug},
    )
    _write_markdown_artifact(edited_path(slug, runs_root), response.content, slug=slug, stage="edited")
    return _stage_result("edit", edited_path(slug, runs_root), response, notes=[])


def run_visual(
    slug: str,
    *,
    client: DeepSeekClient | None = None,
    model: str = "deepseek-chat",
    runs_root: Path = DEFAULT_RUNS_ROOT,
) -> StageResult:
    client = client or DeepSeekClient()
    edited_md = _strip_frontmatter(_read_text(edited_path(slug, runs_root)))

    user_prompt = prompts.VISUAL_USER_TEMPLATE.format(draft_md=edited_md)
    response = client.chat(
        user=user_prompt,
        system=prompts.VISUAL_SYSTEM,
        model=model,
        temperature=0.1,
        response_format="json_object",
        purpose=f"ipo_report:visual:{slug}",
        extra_telemetry={"slug": slug},
    )
    body = response.json_content
    if not isinstance(body, dict):
        raise RuntimeError(f"Visual response was not JSON: {response.content[:200]}")
    write_json(
        visual_path(slug, runs_root),
        {
            **build_envelope("ipo-report/visual_brief.schema", "1.0.0", notes="Stage-6 visual brief."),
            "slug": slug,
            "visual_brief": body,
            "model": response.model,
            "estimated_cost_usd": response.estimated_cost_usd,
        },
    )
    return _stage_result("visual", visual_path(slug, runs_root), response, notes=[])


# --------------------------------------------------------------------- render stage


_PARA_SPLIT = re.compile(r"\n\s*\n")
_FOOTNOTE_TAG = re.compile(r"\[\^(drhp-p\d+)\]")


def _plainify_citations(text: str) -> str:
    """Turn ``[^drhp-p27]`` into ``(DRHP p.27)`` for raw-HTML contexts.

    Footnote references only resolve in the main Markdown flow; inside a
    raw-HTML ``<aside>`` they would render as literal text, so we inline
    the page attribution instead.
    """
    return _FOOTNOTE_TAG.sub(lambda m: f" (DRHP p.{m.group(1).split('p')[-1]})", text or "").strip()


def _markdown_table(columns: list[str], rows: list[list[Any]]) -> str:
    head = "| " + " | ".join(str(c) for c in columns) + " |"
    rule = "| " + " | ".join("---" for _ in columns) + " |"
    body = [
        "| " + " | ".join("" if c is None else str(c) for c in row) + " |"
        for row in rows
    ]
    return "\n".join([head, rule, *body])


def _insert_after_paragraph(body: str, anchor: str | None, block: str) -> tuple[str, bool]:
    """Insert ``block`` after the first paragraph starting with ``anchor``."""
    if not anchor:
        return body, False
    needle = anchor.strip()[:40].lower()
    paras = _PARA_SPLIT.split(body)
    for i, para in enumerate(paras):
        if para.strip().lower().startswith(needle):
            paras.insert(i + 1, block)
            return "\n\n".join(paras), True
    return body, False


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def _yaml_object_list(key: str, items: list[dict[str, Any]], fields: list[str]) -> list[str]:
    if not items:
        return [f"{key}: []"]
    lines = [f"{key}:"]
    for item in items:
        first = True
        for fname in fields:
            if fname not in item:
                continue
            prefix = "  - " if first else "    "
            lines.append(f"{prefix}{fname}: {_yaml_scalar(item.get(fname))}")
            first = False
    return lines


def run_render(
    slug: str,
    *,
    runs_root: Path = DEFAULT_RUNS_ROOT,
    render_root: Path = DEFAULT_RENDER_ROOT,
    status: str | None = None,
) -> StageResult:
    """Stage 7: merge edited draft + visual brief into the published MDX."""
    brief = _read_json(brief_path(slug, runs_root))
    corpus_doc = _read_json(corpus_path(slug, runs_root))
    outline = (_read_json(outline_path(slug, runs_root)).get("outline")) or {}
    visual = (_read_json(visual_path(slug, runs_root)).get("visual_brief")) or {}
    factcheck = (_read_json(factcheck_path(slug, runs_root)).get("report")) or {}
    body = _strip_frontmatter(_read_text(edited_path(slug, runs_root)))

    # The template renders the title in the header; drop a leading H1 so it
    # is not repeated in the body.
    body = re.sub(r"^\s*#\s+.+?\n", "", body, count=1).lstrip("\n")

    # --- inject visual tables into the body ---
    # If the draft already tabularised its data (it is allowed to), do not
    # inject the visual brief's tables too — that produces duplicates.
    draft_has_table = bool(re.search(r"^\|.+\|\s*$", body, re.MULTILINE))
    leftover_tables: list[str] = []
    for table in ([] if draft_has_table else visual.get("tables") or []):
        cols = table.get("columns") or []
        rows = table.get("rows") or []
        if not cols or not rows:
            continue
        title = table.get("title")
        footnote = table.get("footnote")
        block_parts = []
        if title:
            block_parts.append(f"**{title}**")
        block_parts.append(_markdown_table(cols, rows))
        if footnote:
            block_parts.append(f"*{footnote}*")
        block = "\n\n".join(block_parts)
        body, inserted = _insert_after_paragraph(body, table.get("after_paragraph"), block)
        if not inserted:
            leftover_tables.append(block)
    if leftover_tables:
        body = body.rstrip() + "\n\n## Key figures\n\n" + "\n\n".join(leftover_tables)

    # --- inject pull-quotes as inline asides (newspaper styling) ---
    for pq in visual.get("pull_quotes") or []:
        quote = _plainify_citations(pq.get("quote") or "")
        if not quote:
            continue
        block = f'<aside class="pull-quote">{quote}</aside>'
        body, _ = _insert_after_paragraph(body, pq.get("after_paragraph"), block)

    # --- inject sidebars as inline asides after the intro ---
    sidebar_blocks: list[str] = []
    for sb in visual.get("sidebars") or []:
        sb_body = _plainify_citations(sb.get("body_markdown") or "")
        if not sb_body:
            continue
        # Raw-HTML block content is not markdown-processed, so convert inline
        # bold to <strong> and single newlines to <br> ourselves.
        paras = ""
        for chunk in _PARA_SPLIT.split(sb_body):
            chunk = chunk.strip()
            if not chunk:
                continue
            chunk = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", chunk)
            chunk = "<br>".join(line.strip() for line in chunk.split("\n") if line.strip())
            paras += f"<p>{chunk}</p>"
        title = sb.get("title") or "At a glance"
        sidebar_blocks.append(
            '<aside class="article-sidebar">'
            '<div class="article-sidebar-label">At a glance</div>'
            f'<div class="article-sidebar-title">{title}</div>'
            f"{paras}</aside>"
        )
    if sidebar_blocks:
        first_para = _PARA_SPLIT.split(body, 1)
        if len(first_para) == 2:
            body = first_para[0] + "\n\n" + "\n\n".join(sidebar_blocks) + "\n\n" + first_para[1]
        else:
            body = "\n\n".join(sidebar_blocks) + "\n\n" + body

    # --- resolve footnote citations: one definition per cited page ---
    cited_pages = sorted(set(_FOOTNOTE_TAG.findall(body)), key=lambda s: int(s.split("p")[-1]))
    url_by_id = {d["source_id"]: d["url"] for d in (corpus_doc.get("corpus") or [])}
    fallback_url = next(iter(url_by_id.values()), None)
    doc_type = brief.get("document_type", "DRHP")
    footnote_defs = []
    for sid in cited_pages:
        page = sid.split("p")[-1]
        url = url_by_id.get(sid) or fallback_url
        label = f"{doc_type}, page {page}"
        footnote_defs.append(f"[^{sid}]: [{label}]({url})" if url else f"[^{sid}]: {label}")

    # --- frontmatter ---
    def _first(cands: Any, fallback: str) -> str:
        if isinstance(cands, list) and cands:
            return str(cands[0])
        return fallback

    title = _first(outline.get("title_candidates"), brief.get("working_title", slug))
    kicker = _first(outline.get("kicker_candidates"), f"{doc_type} BREAKDOWN")
    dek = _first(outline.get("dek_candidates"), brief.get("dek", ""))
    word_count = len(re.findall(r"\w+", body))
    reading_time = max(1, round(word_count / 200))
    blocking = int(factcheck.get("blocking_count") or 0)
    resolved_status = status or ("published" if blocking == 0 else "review")

    documents = corpus_doc.get("documents") or []

    fm: list[str] = ["---"]
    fm.append(f"slug: {_yaml_scalar(brief.get('issue_slug', slug))}")
    fm.append(f"issue_slug: {_yaml_scalar(brief.get('issue_slug', slug))}")
    fm.append(f"company_name: {_yaml_scalar(brief.get('company_name'))}")
    fm.append(f"document_type: {_yaml_scalar(doc_type)}")
    fm.append("report_type: \"ipo-research\"")
    fm.append(f"kicker: {_yaml_scalar(kicker)}")
    fm.append(f"title: {_yaml_scalar(title)}")
    fm.append(f"dek: {_yaml_scalar(dek)}")
    fm.append(f"reading_time_minutes: {reading_time}")
    fm.append(f"published_at: {_yaml_scalar(utc_now_iso()[:10])}")
    fm.append(f"status: {_yaml_scalar(resolved_status)}")
    fm += _yaml_object_list(
        "sources",
        documents,
        ["source_id", "title", "url", "publisher", "source_tier", "document_kind"],
    )
    fm.append(f"generated_by: {_yaml_scalar(f'{PIPELINE_NAME}/{__version__}')}")
    fm.append("---")

    parts = ["\n".join(fm), body.rstrip()]
    if footnote_defs:
        parts.append("\n".join(footnote_defs))
    mdx = "\n\n".join(parts) + "\n"

    out_path = render_path(slug, render_root)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(mdx, encoding="utf-8")

    notes = [
        f"Rendered {word_count} words (~{reading_time} min), status={resolved_status}, "
        f"{len(cited_pages)} cited pages, {len(visual.get('tables') or [])} table(s)."
    ]
    return StageResult(
        stage="render",
        artifact_path=out_path,
        cached=True,
        model=None,
        tokens_in=0,
        tokens_out=0,
        cost_usd=0.0,
        notes=notes,
    )


# --------------------------------------------------------------------- pipeline

PIPELINE_STAGES = ("outline", "draft", "factcheck", "edit", "visual", "render")


def generate_report(
    issue_slug: str,
    *,
    from_stage: str = "outline",
    to_stage: str = "render",
    runs_root: Path = DEFAULT_RUNS_ROOT,
    render_root: Path = DEFAULT_RENDER_ROOT,
    issues_root: Path | None = None,
    anti_slop_path: Path = DEFAULT_ANTI_SLOP_SPEC_PATH,
    allow_gaps: bool = False,
    allow_blocking: bool = False,
    max_repair_attempts: int = 3,
    client: DeepSeekClient | None = None,
) -> list[StageResult]:
    """Run the report pipeline for one issue slug.

    The ``adapt`` stage always runs first (it is cheap and deterministic
    and the model stages read its artifacts). Then the contiguous range
    ``from_stage``..``to_stage`` runs.
    """
    if from_stage not in PIPELINE_STAGES or to_stage not in PIPELINE_STAGES:
        raise ValueError(f"stages must be within {PIPELINE_STAGES}")
    start, stop = PIPELINE_STAGES.index(from_stage), PIPELINE_STAGES.index(to_stage)
    if stop < start:
        raise ValueError("to_stage must be at or after from_stage")

    client = client or DeepSeekClient()
    run_adapt(issue_slug, runs_root=runs_root, issues_root=issues_root)

    results: list[StageResult] = []
    for stage in PIPELINE_STAGES[start : stop + 1]:
        if stage == "outline":
            results.append(run_outline(issue_slug, client=client, runs_root=runs_root, anti_slop_path=anti_slop_path))
        elif stage == "draft":
            results.append(run_draft(issue_slug, client=client, runs_root=runs_root, anti_slop_path=anti_slop_path, refuse_on_blocking_gaps=not allow_gaps))
        elif stage == "factcheck":
            results.append(run_factcheck(issue_slug, client=client, runs_root=runs_root))
            # Self-heal: re-ground flagged claims and re-check until the gate
            # is clear or attempts run out. This is what lets the full pipeline
            # publish unattended (per "always full pipeline before publish").
            attempt = 0
            while attempt < max_repair_attempts:
                fc = (_read_json(factcheck_path(issue_slug, runs_root)).get("report")) or {}
                if int(fc.get("blocking_count") or 0) == 0:
                    break
                attempt += 1
                results.append(run_repair(issue_slug, client=client, runs_root=runs_root))
                results.append(run_factcheck(issue_slug, client=client, runs_root=runs_root))
        elif stage == "edit":
            # Never crash on persistent blocking in the pipeline. The repair
            # loop above tries to clear the gate; if it cannot, we still edit
            # and render, and the render stage stamps status=review so the
            # report is quarantined from publication (the academy pattern).
            # ``allow_blocking`` only changes whether a leftover-blocking run
            # is forced to status=published downstream.
            results.append(run_edit(issue_slug, client=client, runs_root=runs_root, anti_slop_path=anti_slop_path, refuse_on_blocking_factcheck=False))
        elif stage == "visual":
            results.append(run_visual(issue_slug, client=client, runs_root=runs_root))
        elif stage == "render":
            results.append(run_render(issue_slug, runs_root=runs_root, render_root=render_root))
    return results


def pending_report_slugs(
    *,
    issues_root: Path | None = None,
    render_root: Path = DEFAULT_RENDER_ROOT,
) -> list[str]:
    """Issues whose extraction is usable and whose report is missing or stale.

    A report is stale when its source ``prospectus_facts.json`` was written
    more recently than the rendered ``.md`` — i.e. a fresh extraction needs a
    fresh report. This is the idempotent work-list CI iterates over.
    """
    from .ipo_report_adapter import DEFAULT_ISSUES_ROOT, _load

    root = issues_root or DEFAULT_ISSUES_ROOT
    pending: list[str] = []
    for facts_file in sorted(root.glob("*/prospectus_facts.json")):
        doc = _load(facts_file)
        if not doc.get("facts"):
            continue
        if not (doc.get("quality", {}).get("verified_fact_count") or 0):
            continue
        slug = facts_file.parent.name
        out = render_path(slug, render_root)
        if not out.exists() or facts_file.stat().st_mtime > out.stat().st_mtime:
            pending.append(slug)
    return pending


def generate_pending_reports(
    *,
    limit: int | None = None,
    issues_root: Path | None = None,
    render_root: Path = DEFAULT_RENDER_ROOT,
    client: DeepSeekClient | None = None,
) -> dict[str, Any]:
    """Generate reports for every issue with a new/updated extraction.

    Idempotent and CI-safe: only touches issues whose report is missing or
    older than its extraction; the DeepSeek response cache makes unchanged
    re-runs free. One failing issue never aborts the batch.
    """
    slugs = pending_report_slugs(issues_root=issues_root, render_root=render_root)
    if limit is not None:
        slugs = slugs[:limit]
    client = client or (DeepSeekClient() if slugs else None)

    summary: dict[str, Any] = {"pending": len(slugs), "published": [], "review": [], "failed": []}
    for slug in slugs:
        try:
            generate_report(slug, issues_root=issues_root, render_root=render_root, client=client)
            fc = (_read_json(factcheck_path(slug)).get("report")) or {}
            bucket = "review" if int(fc.get("blocking_count") or 0) else "published"
            summary[bucket].append(slug)
        except Exception as exc:  # one bad filing must not stop the rest
            summary["failed"].append({"slug": slug, "error": f"{type(exc).__name__}: {exc}"})
    return summary


def _main(argv: list[str]) -> int:
    if argv and argv[0] == "--pending":
        limit = int(argv[1]) if len(argv) > 1 else None
        summary = generate_pending_reports(limit=limit)
        print(json.dumps(summary, indent=2))
        return 0
    if not argv:
        print(
            "usage:\n"
            "  python -m ipo_portal.orchestrator.ipo_report <issue-slug> [from_stage] [to_stage]\n"
            "  python -m ipo_portal.orchestrator.ipo_report --pending [limit]",
            file=sys.stderr,
        )
        return 2
    slug = argv[0]
    from_stage = argv[1] if len(argv) > 1 else "outline"
    to_stage = argv[2] if len(argv) > 2 else "render"
    results = generate_report(slug, from_stage=from_stage, to_stage=to_stage)
    total = 0.0
    for r in results:
        total += r.cost_usd
        flag = " (cached)" if r.cached and r.model else ""
        print(f"  {r.stage:10s} -> {r.artifact_path.name}{flag}")
        for n in r.notes:
            print(f"             · {n}")
    print(f"\nTotal model cost this run: ${total:.4f}")
    print(f"Rendered: {render_path(slug)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
