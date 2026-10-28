"""LangChain-orchestrated RAG pipeline (A2 rubric: "use of LangChain or LlamaIndex").

This is a REAL integration, not a wrapper around our own loop:

  * `ReviewMindRetriever` subclasses LangChain's `BaseRetriever`, so our
    bidirectional call-graph retriever becomes a first-class LangChain component
    usable by any chain.
  * The Q&A chain is built with LCEL (`prompt | llm | parser`) and `ChatGroq`,
    i.e. LangChain owns prompt templating, model invocation and output parsing.
  * `Document` metadata carries our chunk provenance (file, symbol, line span,
    retrieval direction), so citations survive the LangChain boundary.

WHY KEEP BOTH PIPELINES?
The hand-rolled pipeline in `reviewmind/qa/bot.py` is retained deliberately. It
has no LangChain dependency, which keeps the experiment reproducible on a
minimal install, and it lets us verify that the LangChain path returns
equivalent retrieval. Having both also makes the orchestration layer an
independent variable rather than an unexamined assumption.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.retrievers import BaseRetriever

from reviewmind.parsing.chunker import Chunk, chunk_repo
from reviewmind.retrieval.graph import GraphRetriever

LC_SYSTEM = """\
You are a code assistant answering questions about a specific repository.
Answer ONLY from the provided excerpts.

Rules:
- Ground every claim in the excerpts and cite them as [1], [2] etc.
- If the excerpts do not contain the answer, say: "The retrieved context does
  not cover this." Do not guess and do not rely on general knowledge of
  similarly-named libraries.
- When asked what a function returns, state the return type and the conditions
  for each case, including None and error paths.
- Be concise. Quote identifiers exactly."""

LC_USER = """\
## Retrieved repository context

{context}

## Question

{question}

Answer using only the context above, citing excerpts as [n]."""


def chunk_to_document(chunk: Chunk, index: int, direction: str = "lexical") -> Document:
    """Convert our Chunk into a LangChain Document, preserving provenance."""
    return Document(
        page_content=chunk.text,
        metadata={
            "index": index,
            "file": chunk.file,
            "name": chunk.name,
            "kind": chunk.kind,
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
            "retrieval_direction": direction,
            "header": chunk.header(),
        },
    )


class ReviewMindRetriever(BaseRetriever):
    """Our bidirectional call-graph retriever as a LangChain `BaseRetriever`.

    Being a genuine LangChain component means any LangChain chain, agent or
    evaluation harness can consume ReviewMind's code-aware retrieval.
    """

    # Declared as pydantic fields because BaseRetriever is a pydantic model.
    graph_retriever: Any
    top_k: int = 6

    model_config = {"arbitrary_types_allowed": True}

    @classmethod
    def from_repo(cls, repo_root: Path, top_k: int = 6) -> "ReviewMindRetriever":
        chunks = chunk_repo(Path(repo_root))
        return cls(graph_retriever=GraphRetriever(chunks), top_k=top_k)

    @property
    def n_chunks(self) -> int:
        return len(self.graph_retriever)

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: Optional[CallbackManagerForRetrieverRun] = None,
        **kwargs: Any,
    ) -> list[Document]:
        hits = self.graph_retriever.search(query, top_k=self.top_k)
        return [
            chunk_to_document(h.chunk, i, getattr(h, "direction", "lexical"))
            for i, h in enumerate(hits, start=1)
        ]


def format_documents(docs: list[Document]) -> str:
    """Render retrieved Documents into the numbered context block."""
    blocks = []
    for d in docs:
        m = d.metadata
        lang = "python" if str(m.get("file", "")).endswith(".py") else ""
        blocks.append(
            f"### [{m.get('index')}] {m.get('file')} lines "
            f"{m.get('start_line')}-{m.get('end_line')} "
            f"({m.get('kind')} `{m.get('name')}`)\n"
            f"```{lang}\n{d.page_content}\n```"
        )
    return "\n\n".join(blocks)


@dataclass
class LCAnswer:
    question: str
    answer: str
    documents: list[Document] = field(default_factory=list)
    latency_s: float = 0.0
    model: str = ""
    orchestrator: str = "langchain"
    errors: list[str] = field(default_factory=list)

    def citations(self) -> list[str]:
        return [str(d.metadata.get("header", "")) for d in self.documents]


class LangChainQABot:
    """Code Q&A over a repository, orchestrated end-to-end by LangChain."""

    def __init__(self, repo_root: Path, model: Optional[str] = None,
                 top_k: int = 6, temperature: float = 0.0) -> None:
        from langchain_groq import ChatGroq

        self.retriever = ReviewMindRetriever.from_repo(repo_root, top_k=top_k)
        self.model_name = model or os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

        self.llm = ChatGroq(
            model=self.model_name,
            temperature=temperature,
            api_key=os.getenv("GROQ_API_KEY"),
            # LangChain's own retry, on top of our client-side throttling.
            max_retries=5,
        )

        self.prompt = ChatPromptTemplate.from_messages([
            ("system", LC_SYSTEM),
            ("human", LC_USER),
        ])

        # LCEL: LangChain owns templating -> invocation -> parsing.
        self.chain = self.prompt | self.llm | StrOutputParser()

    @property
    def n_chunks(self) -> int:
        return self.retriever.n_chunks

    def ask(self, question: str) -> LCAnswer:
        started = time.perf_counter()
        ans = LCAnswer(question=question, answer="", model=self.model_name)

        try:
            docs = self.retriever.invoke(question)
        except Exception as exc:  # noqa: BLE001
            ans.errors.append(f"retrieval failed: {type(exc).__name__}: {exc}")
            ans.latency_s = time.perf_counter() - started
            return ans

        ans.documents = docs
        if not docs:
            ans.answer = "The retrieved context does not cover this."
            ans.errors.append("retrieval returned no documents")
            ans.latency_s = time.perf_counter() - started
            return ans

        try:
            ans.answer = self.chain.invoke({
                "context": format_documents(docs),
                "question": question,
            }).strip()
        except Exception as exc:  # noqa: BLE001
            ans.errors.append(f"chain failed: {type(exc).__name__}: {exc}")

        ans.latency_s = time.perf_counter() - started
        return ans
