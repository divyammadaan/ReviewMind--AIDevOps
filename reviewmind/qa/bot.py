"""Code Q&A bot over a repository (A2 rubric: "RAG-based Q&A bot").

Reuses exactly the same indexing and retrieval stack as the reviewer -- the
`ast` chunker and the bidirectional call-graph retriever -- so the Q&A bot is not
a parallel implementation but a second consumer of one retrieval layer. That
reuse is deliberate: a bug fixed in retrieval improves both, and the ablation
results measured for the reviewer describe the same machinery the bot uses.

Answers are grounded: every response cites the chunks it used, and the system
prompt forbids answering beyond the retrieved context. An ungrounded code
assistant that confidently invents APIs is worse than one that says it does not
know.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

from reviewmind.parsing.chunker import Chunk, chunk_repo
from reviewmind.retrieval.graph import GraphRetriever
from reviewmind.review.prompts import format_chunk

QA_SYSTEM_PROMPT = """\
You are a code assistant answering questions about a specific repository.

You will be given excerpts retrieved from that repository. Answer ONLY from
those excerpts.

Rules:
- Ground every claim in the provided excerpts. Cite them as [1], [2] etc.
- If the excerpts do not contain the answer, say so plainly: "The retrieved
  context does not cover this." Do NOT guess, and do NOT fall back on general
  knowledge of similarly-named libraries.
- When asked what a function returns, state the actual return type and the
  conditions for each case, including None/error paths.
- Be concise and concrete. Quote identifiers exactly as they appear.
- Plain prose, not JSON."""

_QA_USER_TEMPLATE = """\
## Retrieved repository context

{chunks}

## Question

{question}

Answer using only the context above, citing excerpts as [n]."""


@dataclass
class Citation:
    index: int
    file: str
    name: str
    start_line: int
    end_line: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index, "file": self.file, "name": self.name,
            "start_line": self.start_line, "end_line": self.end_line,
        }


@dataclass
class QAAnswer:
    question: str
    answer: str
    citations: list[Citation] = field(default_factory=list)
    latency_s: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    retriever: str = ""
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "answer": self.answer,
            "citations": [c.to_dict() for c in self.citations],
            "latency_s": round(self.latency_s, 3),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "model": self.model,
            "retriever": self.retriever,
            "errors": self.errors,
        }


class CodeQABot:
    """Indexes a repository once, then answers questions against it."""

    def __init__(self, repo_root: Path, llm: Any, top_k: int = 6) -> None:
        self.repo_root = Path(repo_root)
        self.llm = llm
        self.top_k = top_k
        self.chunks: list[Chunk] = chunk_repo(self.repo_root)
        self.retriever = GraphRetriever(self.chunks)

    @property
    def n_chunks(self) -> int:
        return len(self.chunks)

    def retrieve(self, question: str) -> list[Chunk]:
        """Retrieve for a natural-language question.

        No `changed_file`/`exclude_files` here: unlike review, a question has no
        diff, so the whole repository is admissible and the structural caller
        expansion has no anchor. Retrieval is therefore lexical plus callee
        resolution on identifiers named in the question itself.
        """
        hits = self.retriever.search(question, top_k=self.top_k)
        return [h.chunk for h in hits]

    def ask(self, question: str) -> QAAnswer:
        started = time.perf_counter()
        ans = QAAnswer(question=question,
                       answer="",
                       retriever=getattr(self.retriever, "name", "unknown"))

        chunks = self.retrieve(question)
        if not chunks:
            ans.answer = "The retrieved context does not cover this."
            ans.errors.append("retrieval returned no chunks")
            ans.latency_s = time.perf_counter() - started
            return ans

        ans.citations = [
            Citation(index=i, file=c.file, name=c.name,
                     start_line=c.start_line, end_line=c.end_line)
            for i, c in enumerate(chunks, start=1)
        ]

        rendered = "\n\n".join(format_chunk(c, i) for i, c in enumerate(chunks, start=1))
        resp = self.llm.complete(
            QA_SYSTEM_PROMPT,
            _QA_USER_TEMPLATE.format(chunks=rendered, question=question),
            temperature=0.0,
            # Prose, not JSON -- see GroqLLM.complete's docstring.
            json_mode=False,
        )

        ans.model = resp.model
        ans.prompt_tokens = resp.prompt_tokens
        ans.completion_tokens = resp.completion_tokens
        if resp.error:
            ans.errors.append(f"llm error: {resp.error}")
        else:
            ans.answer = (resp.text or "").strip()

        ans.latency_s = time.perf_counter() - started
        return ans
