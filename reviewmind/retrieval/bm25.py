"""Pure-Python BM25 retriever over code chunks.

This is the GUARANTEED retrieval backend: stdlib only, so the experiment can
always run and always be reproduced. The embedding backend (ChromaDB +
sentence-transformers) is an optional upgrade; which backend produced a given
result is recorded in every results file, because the two are not equivalent
and conflating them would misreport the method.

BM25 is lexical, not semantic. For code retrieval this is a smaller handicap
than it sounds -- identifiers are shared vocabulary between a call site and its
definition, which is exactly the signal we need -- but it is a real limitation
and is stated as such in docs/LIMITATIONS.md.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Sequence

from reviewmind.parsing.chunker import Chunk

# Split identifiers the way code is actually written, so that `fetch_user`,
# `fetchUser` and `fetch-user` all yield the tokens {fetch, user}. Without this,
# a call site and its definition can fail to share any token at all.
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

_STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "to", "of", "and", "or", "in", "for", "on",
    "when", "that", "this", "it", "be", "as", "with", "from", "by", "not",
    "self", "def", "class", "return", "import", "none", "true", "false",
})


def tokenize(text: str) -> list[str]:
    """Code-aware tokenisation: split on non-identifier chars, then snake/camel."""
    out: list[str] = []
    for raw in _WORD_RE.findall(text):
        for part in _CAMEL_RE.sub(" ", raw).replace("_", " ").split():
            low = part.lower()
            if len(low) > 1 and low not in _STOPWORDS:
                out.append(low)
    return out


@dataclass
class Hit:
    chunk: Chunk
    score: float


class BM25Retriever:
    """Okapi BM25 over code chunks.

    k1 / b use the standard defaults (1.5 / 0.75). We did not tune them: tuning
    retrieval hyperparameters on the same 10 cases we report results on would
    overfit the evaluation set and inflate the RAG arm.
    """

    name = "bm25"

    def __init__(self, chunks: Sequence[Chunk], k1: float = 1.5, b: float = 0.75) -> None:
        self.chunks = list(chunks)
        self.k1 = k1
        self.b = b

        self._docs: list[list[str]] = [
            tokenize(f"{c.file} {c.name} {c.signature} {c.docstring or ''} {c.text}")
            for c in self.chunks
        ]
        self._tf: list[Counter[str]] = [Counter(d) for d in self._docs]
        self._len: list[int] = [len(d) for d in self._docs]
        self._avg_len = (sum(self._len) / len(self._len)) if self._len else 0.0

        df: Counter[str] = Counter()
        for d in self._docs:
            df.update(set(d))
        n = len(self._docs)
        # BM25 idf with the +0.5 smoothing; floored at a small positive value so
        # that terms appearing in most documents cannot contribute negatively.
        self._idf = {
            term: max(1e-6, math.log(1 + (n - c + 0.5) / (c + 0.5)))
            for term, c in df.items()
        }

    def __len__(self) -> int:
        return len(self.chunks)

    def _score_doc(self, idx: int, q_tokens: Iterable[str]) -> float:
        tf, dl, score = self._tf[idx], self._len[idx], 0.0
        for term in q_tokens:
            f = tf.get(term, 0)
            if not f:
                continue
            idf = self._idf.get(term, 0.0)
            denom = f + self.k1 * (1 - self.b + self.b * dl / (self._avg_len or 1))
            score += idf * (f * (self.k1 + 1)) / denom
        return score

    def search(
        self,
        query: str,
        top_k: int = 5,
        exclude_files: Sequence[str] = (),
    ) -> list[Hit]:
        """Return the top_k chunks for `query`.

        `exclude_files` drops chunks from files already shown to the model. For
        the RAG arm we exclude the changed file itself: the diff is in the prompt
        already, so spending retrieval budget on it would add no information and
        would mask whether cross-file context is what actually helps.
        """
        q = tokenize(query)
        if not q:
            return []
        excluded = {e.replace("\\", "/") for e in exclude_files}

        scored: list[Hit] = []
        for idx, chunk in enumerate(self.chunks):
            if chunk.file.replace("\\", "/") in excluded:
                continue
            s = self._score_doc(idx, q)
            if s > 0:
                scored.append(Hit(chunk=chunk, score=s))

        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:top_k]
