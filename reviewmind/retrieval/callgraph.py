"""Call-graph-aware retrieval expansion.

MOTIVATION -- an empirical finding, not a guess.
When we first evaluated the BM25 retriever on the seeded dataset, it behaved
asymmetrically:

  * `broken_contract_001`: the breaking CALLER (`handle_update_email`) ranked #1.
  * `null_deref_001`:      the relevant CALLEE (`db.fetch_user`) was absent from
                           the top-4 entirely.

The cause is structural. A diff that deletes a `None` check shares identifier
vocabulary with the functions that CALL the changed function, but need not
mention the function it CALLS. `get_user` -> `db.fetch_user` is one hop beyond
the diff's vocabulary, so a purely lexical scorer cannot reach it -- yet
`fetch_user`'s Optional return is precisely what makes the change a defect.

This module closes that gap by resolving callees structurally (via `ast`) rather
than lexically, then unioning them with the BM25 hits.

Reported in docs/LIMITATIONS.md; the BM25-only arm is retained so the
contribution of this expansion is measurable rather than assumed.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from reviewmind.parsing.chunker import Chunk
from reviewmind.retrieval.bm25 import BM25Retriever, Hit

# Lines a unified diff marks as changed (added or removed).
_DIFF_BODY_RE = re.compile(r"^[+-](?![+-])", re.MULTILINE)


def called_names(source: str) -> set[str]:
    """Every function/method name invoked in `source`.

    Resolves both `foo()` and `obj.foo()` to {"foo"}, and records the attribute
    path (`db.fetch_user`) too, so either spelling can match a chunk name.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Name):
            names.add(fn.id)
        elif isinstance(fn, ast.Attribute):
            names.add(fn.attr)
            if isinstance(fn.value, ast.Name):
                names.add(f"{fn.value.id}.{fn.attr}")
    return names


def names_referenced_in_diff(diff: str, changed_file_source: str | None = None) -> set[str]:
    """Callee names reachable from a diff.

    A diff hunk is usually not parseable on its own, so we try the hunk first and
    fall back to the full post-change file. The fallback is deliberately
    permissive: a superset of callees costs a little retrieval budget, whereas
    missing the one that matters costs a detection.
    """
    changed = "\n".join(
        line[1:] for line in diff.splitlines() if _DIFF_BODY_RE.match(line)
    )
    names = called_names(changed)
    if not names and changed_file_source:
        names = called_names(changed_file_source)
    # Bare identifiers in changed lines, for attribute chains `ast` may miss.
    names |= {
        m for m in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", changed)
        if len(m) > 2
    }
    return names


@dataclass
class HybridRetriever:
    """BM25 + structural one-hop callee expansion.

    Retrieval budget is split: `top_k` lexical hits, plus up to `max_callees`
    chunks whose definition name matches a callee of the changed code. Callee
    hits are placed FIRST, because a contract the change depends on is more
    load-bearing than a lexically similar neighbour.
    """

    chunks: Sequence[Chunk]
    max_callees: int = 3
    name = "bm25+callgraph"

    def __post_init__(self) -> None:
        self.bm25 = BM25Retriever(self.chunks)
        # name -> chunk, for definition lookup. Index both "fetch_user" and
        # "db.fetch_user" style keys so either diff spelling resolves.
        self._by_name: dict[str, Chunk] = {}
        for c in self.chunks:
            if c.kind == "module_preamble":
                continue
            self._by_name.setdefault(c.name, c)
            self._by_name.setdefault(c.name.split(".")[-1], c)
            module = Path(c.file).stem
            self._by_name.setdefault(f"{module}.{c.name}", c)

    def __len__(self) -> int:
        return len(self.chunks)

    def search(
        self,
        query: str,
        top_k: int = 5,
        exclude_files: Sequence[str] = (),
        changed_file_source: str | None = None,
    ) -> list[Hit]:
        excluded = {e.replace("\\", "/") for e in exclude_files}

        # --- structural: definitions of things the changed code calls --------
        callee_hits: list[Hit] = []
        seen: set[str] = set()
        for name in sorted(names_referenced_in_diff(query, changed_file_source)):
            chunk = self._by_name.get(name)
            if chunk is None or chunk.ident in seen:
                continue
            if chunk.file.replace("\\", "/") in excluded:
                continue
            seen.add(chunk.ident)
            # score=inf marks a structural hit; it is not a BM25 score and is
            # never compared against one.
            callee_hits.append(Hit(chunk=chunk, score=float("inf")))
            if len(callee_hits) >= self.max_callees:
                break

        # --- lexical: BM25, filling the remaining budget --------------------
        lexical = [
            h for h in self.bm25.search(query, top_k=top_k + len(callee_hits),
                                        exclude_files=tuple(excluded))
            if h.chunk.ident not in seen
        ]

        return (callee_hits + lexical)[:top_k]
