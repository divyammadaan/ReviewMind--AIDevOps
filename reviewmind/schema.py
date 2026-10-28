"""Shared data types.

Every arm -- static analyser, diff-only LLM, RAG LLM -- emits `Issue` objects.
A single output type is what makes the three arms comparable under one scorer;
if each arm had its own shape, any measured difference could be an artefact of
parsing rather than of review quality.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Optional

Severity = Literal["critical", "high", "medium", "low", "info"]


@dataclass
class Issue:
    """One reported problem, located in a file."""

    file: str
    line: Optional[int]
    severity: Severity
    category: str
    message: str
    suggestion: str = ""
    # Provenance: which arm/tool produced this, for auditing results.
    source: str = ""
    rule_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TestSuggestion:
    """A suggested test case (A2 rubric: AI-assisted test generation)."""

    target: str
    name: str
    rationale: str
    code: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReviewResult:
    """Everything one arm produced for one case, plus cost telemetry."""

    arm: str
    case_id: str
    issues: list[Issue] = field(default_factory=list)
    test_suggestions: list[TestSuggestion] = field(default_factory=list)

    # Cost / reproducibility telemetry (RQ3)
    latency_s: float = 0.0
    retrieval_s: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    retriever: str = ""
    # Non-fatal problems (e.g. malformed LLM JSON). Recorded, never hidden:
    # a run that silently dropped failures would misstate detection rates.
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "case_id": self.case_id,
            "issues": [i.to_dict() for i in self.issues],
            "test_suggestions": [t.to_dict() for t in self.test_suggestions],
            "latency_s": round(self.latency_s, 3),
            "retrieval_s": round(self.retrieval_s, 4),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "model": self.model,
            "retriever": self.retriever,
            "errors": self.errors,
        }
