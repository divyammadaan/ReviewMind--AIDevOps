"""Arms B (diff-only) and C (RAG) -- LLM review.

Both arms are produced by THIS ONE function, differing only in whether a
retriever is supplied. That is a deliberate structural guarantee for RQ2: there
is no separate code path in which the prompt, model, temperature or parsing
could drift between arms.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional, Sequence

from reviewmind.parsing.chunker import Chunk
from reviewmind.review.llm import extract_json
from reviewmind.review.prompts import SYSTEM_PROMPT, build_user_prompt
from reviewmind.schema import Issue, ReviewResult, TestSuggestion

VALID_SEVERITY = {"critical", "high", "medium", "low", "info"}

# Hard ceiling on retrieved context, in characters (~4 chars per token).
# WHY: retrieval returns the top-k most relevant chunks, but says nothing about
# their SIZE. Reviewing a PR against a large repository pulled 36,328 tokens of
# context and was rejected with HTTP 413 against an 8,000 tokens/minute limit.
# Relevance ranking alone is not a budget, so we impose one: oversized chunks
# are truncated, and once the budget is spent the remaining chunks are dropped.
# Chunks are consumed in rank order, so the most relevant context survives.
MAX_CONTEXT_CHARS = 10_000
MAX_CHUNK_CHARS = 2_500


def fit_context(chunks: list[Chunk]) -> tuple[list[Chunk], int]:
    """Trim retrieved chunks to the context budget. Returns (chunks, n_dropped)."""
    import dataclasses

    kept: list[Chunk] = []
    spent = 0
    dropped = 0

    for c in chunks:
        text = c.text
        if len(text) > MAX_CHUNK_CHARS:
            text = text[:MAX_CHUNK_CHARS] + "\n    # ... (truncated)"
        if spent + len(text) > MAX_CONTEXT_CHARS:
            dropped += 1
            continue
        kept.append(c if text == c.text else dataclasses.replace(c, text=text))
        spent += len(text)

    return kept, dropped


def _coerce_line(raw: Any) -> Optional[int]:
    """LLMs emit line numbers as ints, strings, or '42-45'. Normalise to int."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    if isinstance(raw, str):
        token = raw.strip().split("-")[0].strip()
        if token.isdigit():
            return int(token)
    return None


def parse_issues(payload: dict[str, Any], source: str) -> tuple[list[Issue], list[str]]:
    """Convert raw LLM JSON into Issue objects. Returns (issues, warnings)."""
    issues: list[Issue] = []
    warnings: list[str] = []

    raw_issues = payload.get("issues")
    if raw_issues is None:
        return [], ["response contained no 'issues' key"]
    if not isinstance(raw_issues, list):
        return [], [f"'issues' was {type(raw_issues).__name__}, expected list"]

    for idx, item in enumerate(raw_issues):
        if not isinstance(item, dict):
            warnings.append(f"issue[{idx}] was not an object")
            continue
        sev = str(item.get("severity", "medium")).lower()
        if sev not in VALID_SEVERITY:
            sev = "medium"
        issues.append(Issue(
            file=str(item.get("file", "")),
            line=_coerce_line(item.get("line")),
            severity=sev,  # type: ignore[arg-type]
            category=str(item.get("category", "unspecified")),
            message=str(item.get("message", "")),
            suggestion=str(item.get("suggestion", "")),
            source=source,
        ))
    return issues, warnings


def parse_tests(payload: dict[str, Any]) -> list[TestSuggestion]:
    out: list[TestSuggestion] = []
    for item in payload.get("test_suggestions") or []:
        if isinstance(item, dict):
            out.append(TestSuggestion(
                target=str(item.get("target", "")),
                name=str(item.get("name", "")),
                rationale=str(item.get("rationale", "")),
                code=str(item.get("code", "")),
            ))
    return out


def review(
    case_id: str,
    diff: str,
    llm: Any,
    retriever: Any = None,
    repo_dir: Optional[Path] = None,
    changed_file: Optional[str] = None,
    top_k: int = 6,
    temperature: float = 0.0,
) -> ReviewResult:
    """Review one diff.

    `retriever=None` -> Arm B (llm_diff). A retriever -> Arm C (llm_rag).
    """
    arm = "llm_rag" if retriever is not None else "llm_diff"
    result = ReviewResult(arm=arm, case_id=case_id)
    started = time.perf_counter()

    # --- retrieval (Arm C only) ------------------------------------------- #
    chunks: Sequence[Chunk] = []
    retrieval_s = 0.0
    if retriever is not None:
        r0 = time.perf_counter()
        kwargs: dict[str, Any] = {
            "top_k": top_k,
            # Exclude the changed file: its content is already in the diff, so
            # spending budget there would not test cross-file context.
            "exclude_files": [changed_file] if changed_file else [],
        }
        # Pass the richer arguments only to retrievers that accept them.
        if changed_file and repo_dir is not None:
            src_path = repo_dir / changed_file
            if src_path.exists():
                kwargs["changed_file_source"] = src_path.read_text(
                    encoding="utf-8", errors="replace")
        if type(retriever).__name__ == "GraphRetriever" and changed_file:
            kwargs["changed_file"] = changed_file
        elif type(retriever).__name__ == "BM25Retriever":
            kwargs.pop("changed_file_source", None)

        try:
            hits = retriever.search(diff, **kwargs)
            chunks, dropped = fit_context([h.chunk for h in hits])
            if dropped:
                result.errors.append(
                    f"context budget: dropped {dropped} chunk(s) over "
                    f"{MAX_CONTEXT_CHARS} chars")
        except (TypeError, ValueError, OSError) as exc:
            result.errors.append(f"retrieval failed: {exc}")
        retrieval_s = time.perf_counter() - r0
        result.retriever = getattr(retriever, "name", type(retriever).__name__)
    else:
        result.retriever = "none"

    # --- generation -------------------------------------------------------- #
    user_prompt = build_user_prompt(diff, chunks if retriever is not None else None)
    resp = llm.complete(SYSTEM_PROMPT, user_prompt, temperature=temperature)

    result.model = resp.model
    result.prompt_tokens = resp.prompt_tokens
    result.completion_tokens = resp.completion_tokens

    if resp.error:
        result.errors.append(f"llm error: {resp.error}")
        result.latency_s = time.perf_counter() - started
        return result

    payload, parse_err = extract_json(resp.text)
    if parse_err:
        result.errors.append(f"parse: {parse_err}")

    issues, warnings = parse_issues(payload, source=arm)
    result.issues = issues
    result.test_suggestions = parse_tests(payload)
    result.errors.extend(warnings)

    result.latency_s = time.perf_counter() - started
    # Retrieval time is part of the arm's cost and is reported separately so the
    # RQ3 comparison can separate retrieval overhead from generation time.
    result.retrieval_s = retrieval_s  # type: ignore[attr-defined]
    return result
