"""Scoring for the ReviewMind experiment. Stdlib only.

Implements the matching rule fixed in docs/RESEARCH_DESIGN.md §6.4:

    A seeded defect counts as DETECTED when some reported issue
      (a) names the correct file, and
      (b) cites a line within +/- TOLERANCE of the injected SPAN.

The tolerance was fixed before results were examined. `sensitivity_sweep()`
re-scores at +/-0, +/-3 and +/-10 so the report can show that conclusions do not
hinge on that choice.

METHODOLOGY NOTE (recorded for transparency, see docs/RESEARCH_DESIGN.md §6.4):
the ground truth was originally a single line -- the first changed line of the
diff. Smoke-testing the static baseline revealed this to be a LABEL DEFECT, not
a tolerance problem: `sql_injection_001` injects a multi-line function at line 29
whose vulnerable query sits at line 33, so ruff's correct S608 detection scored
as a miss. The label became a span (the full set of changed lines, derived
mechanically via difflib).

This change was made AFTER observing static-arm output, which is exactly the kind
of post-hoc adjustment that can bias a result. Three facts constrain it:
  1. It corrects a wrong label; it does not tune the tolerance.
  2. It applies identically to all arms.
  3. It was made BEFORE any LLM arm had ever been run, so it cannot have been
     fitted to favour ReviewMind.
If anything it HELPS the baseline, which is the opposite of a self-serving bias.

On precision: our precision is a LOWER BOUND. An arm may flag a genuine
pre-existing problem we did not seed; the matcher counts that as a false
positive. This understates arms that review broadly (the LLM arms) and flatters
narrow ones. Never present `precision` here as final without the audit
correction described in §6.2.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bugs.concepts import satisfies_rubric  # noqa: E402

DEFAULT_TOLERANCE = 3
SENSITIVITY_TOLERANCES = (0, 3, 10)


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #

def _normalise_path(p: str) -> str:
    """Compare paths by suffix.

    Arms report paths inconsistently: bandit emits an absolute path, the LLM
    tends to echo the path as written in the diff header. Comparing basenames
    plus one parent directory is tolerant of that without being so loose that
    same-named files in different packages collide.
    """
    parts = [seg for seg in p.replace("\\", "/").split("/") if seg not in ("", ".")]
    return "/".join(parts[-2:]) if len(parts) >= 2 else "/".join(parts)


def issue_matches_defect(
    issue_file: str,
    issue_line: Optional[int],
    defect_file: str,
    defect_span: tuple[int, int] | int,
    tolerance: int = DEFAULT_TOLERANCE,
) -> bool:
    """True when `issue_line` falls within `defect_span` widened by `tolerance`.

    `defect_span` accepts a bare int for backwards compatibility, treated as a
    one-line span.
    """
    if _normalise_path(issue_file) != _normalise_path(defect_file):
        return False
    if issue_line is None:
        # A file-level report with no line cannot be credited as a located
        # detection; doing so would let an arm score by flagging whole files.
        return False

    if isinstance(defect_span, int):
        start = end = defect_span
    else:
        start, end = defect_span

    return (int(start) - tolerance) <= int(issue_line) <= (int(end) + tolerance)


# --------------------------------------------------------------------------- #
# Confidence intervals
# --------------------------------------------------------------------------- #

def wilson_interval(successes: int, trials: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a binomial proportion.

    Chosen over the normal approximation because our N is small (tens of cases)
    and rates near 0 or 1 are likely -- exactly where the normal approximation
    produces impossible bounds outside [0, 1].
    """
    if trials == 0:
        return (0.0, 0.0)
    p = successes / trials
    denom = 1 + z**2 / trials
    centre = (p + z**2 / (2 * trials)) / denom
    margin = (z * math.sqrt(p * (1 - p) / trials + z**2 / (4 * trials**2))) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #

def defect_span_of(case: dict[str, Any]) -> tuple[int, int]:
    """Read the span from a case record, tolerating the older single-line format."""
    span = case.get("injected_span")
    if span:
        return (int(span[0]), int(span[1]))
    line = int(case["injected_line"])
    return (line, line)


@dataclass
class CaseScore:
    case_id: str
    defect_class: str
    requires_cross_file_context: bool
    detected: bool               # positional match only
    n_issues_reported: int
    detected_semantic: bool = False   # positional AND describes the real defect
    injection_mode: str = "deletion"
    matched_issue_index: Optional[int] = None
    latency_s: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class ArmScore:
    arm: str
    tolerance: int
    cases: list[CaseScore] = field(default_factory=list)

    # -- primary metrics ---------------------------------------------------- #
    @property
    def n_cases(self) -> int:
        return len(self.cases)

    @property
    def n_detected(self) -> int:
        return sum(1 for c in self.cases if c.detected)

    @property
    def n_detected_semantic(self) -> int:
        return sum(1 for c in self.cases if c.detected_semantic)

    @property
    def recall_semantic(self) -> float:
        """Recall requiring the report to describe the ACTUAL defect.

        This is the stricter and more meaningful figure. Positional recall
        over-credits a vague guess that happens to land near the right line.
        """
        return self.n_detected_semantic / self.n_cases if self.n_cases else 0.0

    @property
    def recall_semantic_ci(self) -> tuple[float, float]:
        return wilson_interval(self.n_detected_semantic, self.n_cases)

    @property
    def recall(self) -> float:
        return self.n_detected / self.n_cases if self.n_cases else 0.0

    @property
    def recall_ci(self) -> tuple[float, float]:
        return wilson_interval(self.n_detected, self.n_cases)

    @property
    def total_issues_reported(self) -> int:
        return sum(c.n_issues_reported for c in self.cases)

    @property
    def precision_lower_bound(self) -> float:
        """Matched issues / all reported issues. A LOWER BOUND -- see module docstring."""
        total = self.total_issues_reported
        return (self.n_detected / total) if total else 0.0

    @property
    def f1_lower_bound(self) -> float:
        p, r = self.precision_lower_bound, self.recall
        return (2 * p * r / (p + r)) if (p + r) else 0.0

    @property
    def issues_per_case(self) -> float:
        return self.total_issues_reported / self.n_cases if self.n_cases else 0.0

    # -- cost (RQ3) --------------------------------------------------------- #
    @property
    def median_latency_s(self) -> float:
        if not self.cases:
            return 0.0
        xs = sorted(c.latency_s for c in self.cases)
        mid = len(xs) // 2
        return xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2

    @property
    def p90_latency_s(self) -> float:
        if not self.cases:
            return 0.0
        xs = sorted(c.latency_s for c in self.cases)
        return xs[min(len(xs) - 1, int(0.9 * len(xs)))]

    @property
    def total_tokens(self) -> int:
        return sum(c.prompt_tokens + c.completion_tokens for c in self.cases)

    # -- breakdowns that test H1-H3 ----------------------------------------- #
    def recall_by_class(self) -> dict[str, tuple[int, int]]:
        out: dict[str, list[int]] = {}
        for c in self.cases:
            slot = out.setdefault(c.defect_class, [0, 0])
            slot[1] += 1
            if c.detected:
                slot[0] += 1
        return {k: (v[0], v[1]) for k, v in out.items()}

    def recall_by_context_need(self) -> dict[str, tuple[int, int]]:
        """Split by whether the defect needs cross-file context. Tests H3."""
        out = {"cross_file": [0, 0], "local": [0, 0]}
        for c in self.cases:
            key = "cross_file" if c.requires_cross_file_context else "local"
            out[key][1] += 1
            if c.detected:
                out[key][0] += 1
        return {k: (v[0], v[1]) for k, v in out.items()}

    def semantic_by_injection_mode(self) -> dict[str, tuple[int, int]]:
        out: dict[str, list[int]] = {}
        for c in self.cases:
            slot = out.setdefault(c.injection_mode, [0, 0])
            slot[1] += 1
            if c.detected_semantic:
                slot[0] += 1
        return {k: (v[0], v[1]) for k, v in out.items()}

    def semantic_cross_file_additive(self) -> tuple[int, int]:
        sel = [c for c in self.cases
               if c.injection_mode == "addition" and c.requires_cross_file_context]
        return (sum(1 for c in sel if c.detected_semantic), len(sel))

    def recall_by_injection_mode(self) -> dict[str, tuple[int, int]]:
        """Split by how the defect was created. THE key breakdown for RQ2.

        Deletion-mode defects leak their own ground truth into the diff (the
        removed lines state the violated contract), so they cannot test whether
        retrieval supplies missing context. Addition-mode defects do not leak,
        so only the addition-mode rows are a valid test of RQ2.
        """
        out: dict[str, list[int]] = {}
        for c in self.cases:
            slot = out.setdefault(c.injection_mode, [0, 0])
            slot[1] += 1
            if c.detected:
                slot[0] += 1
        return {k: (v[0], v[1]) for k, v in out.items()}

    def recall_cross_file_additive(self) -> tuple[int, int]:
        """Recall on the cases that genuinely require external context."""
        sel = [c for c in self.cases
               if c.injection_mode == "addition" and c.requires_cross_file_context]
        return (sum(1 for c in sel if c.detected), len(sel))

    def to_dict(self) -> dict[str, Any]:
        lo, hi = self.recall_ci
        return {
            "arm": self.arm,
            "tolerance": self.tolerance,
            "n_cases": self.n_cases,
            "n_detected": self.n_detected,
            "recall": round(self.recall, 4),
            "recall_ci95": [round(lo, 4), round(hi, 4)],
            "n_detected_semantic": self.n_detected_semantic,
            "recall_semantic": round(self.recall_semantic, 4),
            "recall_semantic_ci95": [round(x, 4) for x in self.recall_semantic_ci],
            "precision_lower_bound": round(self.precision_lower_bound, 4),
            "f1_lower_bound": round(self.f1_lower_bound, 4),
            "total_issues_reported": self.total_issues_reported,
            "issues_per_case": round(self.issues_per_case, 2),
            "median_latency_s": round(self.median_latency_s, 3),
            "p90_latency_s": round(self.p90_latency_s, 3),
            "total_tokens": self.total_tokens,
            "recall_by_class": {
                k: {"detected": d, "total": t, "recall": round(d / t, 4) if t else 0.0}
                for k, (d, t) in sorted(self.recall_by_class().items())
            },
            "recall_by_context_need": {
                k: {"detected": d, "total": t, "recall": round(d / t, 4) if t else 0.0}
                for k, (d, t) in self.recall_by_context_need().items()
            },
            "recall_by_injection_mode": {
                k: {"detected": d, "total": t, "recall": round(d / t, 4) if t else 0.0}
                for k, (d, t) in sorted(self.recall_by_injection_mode().items())
            },
            "recall_cross_file_additive": list(self.recall_cross_file_additive()),
            "semantic_by_injection_mode": {
                k: {"detected": d, "total": t, "recall": round(d / t, 4) if t else 0.0}
                for k, (d, t) in sorted(self.semantic_by_injection_mode().items())
            },
            "semantic_cross_file_additive": list(self.semantic_cross_file_additive()),
        }


def score_arm(
    arm: str,
    results: Iterable[dict[str, Any]],
    cases_by_id: dict[str, dict[str, Any]],
    tolerance: int = DEFAULT_TOLERANCE,
) -> ArmScore:
    """Score one arm's raw results against ground truth.

    `results`      -- ReviewResult.to_dict() payloads
    `cases_by_id`  -- case_id -> case.json record (the labels)
    """
    score = ArmScore(arm=arm, tolerance=tolerance)

    for res in results:
        case = cases_by_id.get(res["case_id"])
        if case is None:
            continue

        matched_idx = None
        semantic = False
        for idx, issue in enumerate(res.get("issues", [])):
            if not issue_matches_defect(
                issue.get("file", ""), issue.get("line"),
                case["target_file"], defect_span_of(case),
                tolerance=tolerance,
            ):
                continue
            if matched_idx is None:
                matched_idx = idx
            # A semantic detection needs a positionally-matched issue that ALSO
            # describes the real defect. Any matched issue may satisfy it, so an
            # arm is not penalised for listing the correct diagnosis second.
            if satisfies_rubric(res["case_id"], issue.get("message", ""),
                                issue.get("suggestion", "")):
                semantic = True
                break

        score.cases.append(CaseScore(
            case_id=res["case_id"],
            defect_class=case["defect_class"],
            requires_cross_file_context=case["requires_cross_file_context"],
            injection_mode=case.get("injection_mode", "deletion"),
            detected=matched_idx is not None,
            n_issues_reported=len(res.get("issues", [])),
            detected_semantic=semantic,
            matched_issue_index=matched_idx,
            latency_s=res.get("latency_s", 0.0),
            prompt_tokens=res.get("prompt_tokens", 0),
            completion_tokens=res.get("completion_tokens", 0),
        ))

    return score


def sensitivity_sweep(
    arm: str,
    results: list[dict[str, Any]],
    cases_by_id: dict[str, dict[str, Any]],
) -> dict[int, float]:
    """Recall at each tolerance, to show robustness to the matching window."""
    return {
        tol: score_arm(arm, results, cases_by_id, tolerance=tol).recall
        for tol in SENSITIVITY_TOLERANCES
    }
