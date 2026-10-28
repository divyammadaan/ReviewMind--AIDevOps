"""Bidirectional call-graph retrieval.

DESIGN RATIONALE -- derived from two measured failures, recorded in order so the
reasoning can be audited:

  1. BM25 alone (`bm25.py`) ranked breaking CALLERS highly but missed CALLEES.
     A diff shares vocabulary with code that calls it, not necessarily with the
     code it calls -- yet `null_deref_001` is only a defect because of
     `db.fetch_user`'s Optional return, one hop downstream.

  2. Adding callee-only expansion (`callgraph.py`) fixed that case but REGRESSED
     caller cases: `broken_contract_002` fell out of the top-5 because structural
     callee hits displaced the lexical hit for `handle_user_summary`.

Both failures share one cause: a code change can break things in BOTH
directions. What it CALLS determines whether the change is internally valid;
what CALLS IT determines whether the change breaks its contract. Retrieving one
direction trades away the other.

This module resolves both directions structurally and reserves separate budget
for each, so neither displaces the other.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Sequence

from reviewmind.parsing.chunker import Chunk
from reviewmind.retrieval.bm25 import BM25Retriever, Hit
from reviewmind.retrieval.callgraph import called_names, names_referenced_in_diff

Direction = Literal["callee", "caller", "lexical"]


@dataclass
class GraphHit(Hit):
    """A Hit annotated with WHY it was retrieved.

    Provenance is not cosmetic: it lets the results analysis attribute a
    detection to structural vs lexical retrieval, which is what makes the
    ablation interpretable rather than just a number.
    """

    direction: Direction = "lexical"
    via: str = ""


@dataclass
class CallGraph:
    """Which chunk calls which, built with `ast` over the indexed chunks."""

    chunks: Sequence[Chunk]
    _calls: dict[str, set[str]] = field(default_factory=dict)
    _callers_of: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    _by_name: dict[str, Chunk] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for c in self.chunks:
            if c.kind != "module_preamble":
                short = c.name.split(".")[-1]
                self._by_name.setdefault(c.name, c)
                self._by_name.setdefault(short, c)
                self._by_name.setdefault(f"{Path(c.file).stem}.{c.name}", c)

        for c in self.chunks:
            callees = called_names(c.text) if c.kind != "module_preamble" else set()
            self._calls[c.ident] = callees
            for callee in callees:
                target = self._by_name.get(callee)
                # Ignore self-edges: a recursive function is not its own context.
                if target is not None and target.ident != c.ident:
                    self._callers_of[target.ident].add(c.ident)

        self._by_ident = {c.ident: c for c in self.chunks}

    def callees_of_names(self, names: set[str]) -> list[tuple[Chunk, str]]:
        """Definitions of the functions named in `names`."""
        out, seen = [], set()
        for n in sorted(names):
            chunk = self._by_name.get(n)
            if chunk is not None and chunk.ident not in seen:
                seen.add(chunk.ident)
                out.append((chunk, n))
        return out

    def callers_of_chunks(self, targets: Sequence[Chunk]) -> list[tuple[Chunk, str]]:
        """Chunks that call any of `targets` -- i.e. who depends on this change."""
        out, seen = [], set()
        for t in targets:
            for ident in sorted(self._callers_of.get(t.ident, ())):
                if ident in seen:
                    continue
                seen.add(ident)
                caller = self._by_ident.get(ident)
                if caller is not None:
                    out.append((caller, t.name))
        return out


@dataclass
class GraphRetriever:
    """Bidirectional structural retrieval + BM25, with reserved budgets.

    Budget allocation (for the default top_k=6):
        up to `max_callees` (2)  structural -- what the change DEPENDS ON
        up to `max_callers` (2)  structural -- what DEPENDS ON the change
        remainder                lexical BM25 -- everything else

    Reserving separate budget per direction is the fix for failure (2) above:
    structural hits can no longer crowd each other or the lexical tail out of
    the window.
    """

    chunks: Sequence[Chunk]
    max_callees: int = 2
    max_callers: int = 2
    name = "bm25+bidirectional-callgraph"

    def __post_init__(self) -> None:
        self.bm25 = BM25Retriever(self.chunks)
        self.graph = CallGraph(self.chunks)

    def __len__(self) -> int:
        return len(self.chunks)

    def _changed_chunks(self, changed_file: str, diff: str) -> list[Chunk]:
        """Chunks of the changed file overlapping the diff's changed lines.

        These are the targets whose CALLERS we want. Falls back to all chunks in
        the changed file when hunk headers cannot be parsed.
        """
        touched: set[int] = set()
        cur = 0
        for line in diff.splitlines():
            if line.startswith("@@"):
                try:
                    seg = line.split("+")[1].split("@@")[0].strip()
                    cur = int(seg.split(",")[0])
                except (IndexError, ValueError):
                    cur = 0
            elif line.startswith("+") and not line.startswith("+++"):
                if cur:
                    touched.add(cur)
                    cur += 1
            elif not line.startswith("-") and cur:
                cur += 1

        same_file = [
            c for c in self.chunks
            if c.file.replace("\\", "/") == changed_file.replace("\\", "/")
            and c.kind != "module_preamble"
        ]
        if not touched:
            return same_file
        overlapping = [
            c for c in same_file
            if any(c.start_line <= ln <= c.end_line for ln in touched)
        ]
        return overlapping or same_file

    def search(
        self,
        query: str,
        top_k: int = 6,
        exclude_files: Sequence[str] = (),
        changed_file_source: str | None = None,
        changed_file: str | None = None,
    ) -> list[GraphHit]:
        excluded = {e.replace("\\", "/") for e in exclude_files}
        seen: set[str] = set()
        callee_hits: list[GraphHit] = []
        caller_hits: list[GraphHit] = []

        def admissible(chunk: Chunk) -> bool:
            return (chunk.ident not in seen
                    and chunk.file.replace("\\", "/") not in excluded)

        # --- direction 1: what the changed code CALLS -----------------------
        names = names_referenced_in_diff(query, changed_file_source)
        for chunk, via in self.graph.callees_of_names(names):
            if len(callee_hits) >= self.max_callees:
                break
            if admissible(chunk):
                seen.add(chunk.ident)
                callee_hits.append(GraphHit(chunk=chunk, score=float("inf"),
                                            direction="callee", via=via))

        # --- direction 2: what CALLS the changed code -----------------------
        if changed_file:
            targets = self._changed_chunks(changed_file, query)
            for chunk, via in self.graph.callers_of_chunks(targets):
                if len(caller_hits) >= self.max_callers:
                    break
                if admissible(chunk):
                    seen.add(chunk.ident)
                    caller_hits.append(GraphHit(chunk=chunk, score=float("inf"),
                                                direction="caller", via=via))

        # --- remainder: lexical ---------------------------------------------
        structural = callee_hits + caller_hits
        room = max(0, top_k - len(structural))
        lexical = [
            GraphHit(chunk=h.chunk, score=h.score, direction="lexical")
            for h in self.bm25.search(query, top_k=room + len(structural),
                                      exclude_files=tuple(excluded))
            if h.chunk.ident not in seen
        ][:room]

        return structural + lexical
