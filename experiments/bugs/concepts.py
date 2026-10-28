"""Concept rubrics: does a reported issue describe the ACTUAL seeded defect?

WHY THIS EXISTS
---------------
Positional matching (file + line within tolerance) proved to over-credit the
diff-only arm. Observed in run_20261004T105129Z:

  add_double_scale_001
    llm_diff: "If completion_ratio raises ... no error handling"   <- WRONG defect
    llm_rag:  "completion_ratio already returns a percentage;
               multiplying by 100 produces 5000%"                  <- CORRECT

  add_ignored_return_001
    llm_diff: "Catches all Exception types ... KeyboardInterrupt"   <- WRONG defect
    llm_rag:  "update_email's boolean return value is ignored;
               failures reported as success"                       <- CORRECT

Both llm_diff reports landed within the +/-3 line window while diagnosing
something else entirely, so both scored as detections. Recall measured LOCATION,
not UNDERSTANDING -- and the arm with less context benefits most from that
leniency, because a vague nearby guess is exactly what a context-starved
reviewer produces.

DESIGN
------
Each defect declares concept groups. A report counts as a SEMANTIC detection
when it satisfies positional matching AND mentions at least one term from EVERY
required group. Groups are conjunctive (all must appear), terms within a group
are disjunctive (any synonym counts).

Rubrics are derived from each defect's `why_wrong` field, which was written
before any arm was run. They are deterministic and auditable -- no LLM judge, so
there is no circularity in using a model to grade a model.

LIMITATION, stated plainly: keyword rubrics can produce false negatives when a
correct diagnosis uses unanticipated wording. Semantic recall is therefore a
LOWER BOUND on true understanding, just as positional precision is a lower
bound. Reporting both bounds brackets the truth rather than pretending to one
exact number.
"""

from __future__ import annotations

import re

# case_id -> tuple of concept groups; each group is a tuple of accepted terms.
CONCEPT_RUBRIC: dict[str, tuple[tuple[str, ...], ...]] = {
    # ---- deletion-mode -------------------------------------------------- #
    "hardcoded_secret_001": (
        ("hardcod", "hard-cod", "plaintext", "plain text", "in source", "literal"),
        ("secret", "password", "credential", "token", "api key", "apikey"),
    ),
    "bare_except_001": (
        ("bare except", "bare `except`", "except:", "broad except", "catch-all",
         "catches all", "all exception"),
    ),
    "sql_injection_001": (
        ("sql injection", "sqli", "injection"),
        ("concat", "string", "interpolat", "parameteri", "f-string", "+"),
    ),
    "off_by_one_001": (
        ("limit", "length", "len", "character", "too long", "longer"),
        ("off-by-one", "off by one", "limit - 2", "limit-2", "exceed", "longer than",
         "one more", "limit + 1", "limit+1", "3 character", "ellipsis"),
    ),
    "off_by_one_002": (
        ("skip", "miss", "never compar", "second", "index 2", "tasks[2:]", "omit",
         "ignores", "excludes"),
    ),
    "missing_error_handling_001": (
        ("zero", "zerodivision", "division by zero", "divide by zero", "whole == 0",
         "whole is 0", "0.0 guard", "guard"),
    ),
    "null_deref_001": (
        ("none", "null", "missing user", "does not exist", "nonexistent"),
        ("typeerror", "subscript", "index", "deref", "access", "crash", "raise",
         "unknown", "check"),
    ),
    "null_deref_002": (
        ("none", "null", "missing task", "does not exist", "nonexistent"),
        ("typeerror", "subscript", "index", "deref", "access", "crash", "raise",
         "check"),
    ),
    "broken_contract_001": (
        ("raise", "exception", "valueerror", "lookuperror", "throw"),
        ("return false", "boolean", "bool", "caller", "contract", "400",
         "handler", "uncaught", "propagat"),
    ),
    "broken_contract_002": (
        ("percent", "percentage", "100", "fraction", "0-1", "0 to 1", "ratio"),
    ),

    # ---- addition-mode (the valid RQ2 test bed) ------------------------- #
    "add_null_deref_001": (
        ("none", "null", "missing user", "does not exist", "nonexistent"),
        ("typeerror", "subscript", "deref", "crash", "without a none check",
         "no none check", "check", "raise"),
    ),
    "add_null_deref_002": (
        ("none", "null", "missing task", "does not exist", "nonexistent"),
        ("typeerror", "subscript", "deref", "crash", "check", "raise"),
    ),
    "add_double_scale_001": (
        # A correct diagnosis MUST recognise the unit convention of the callee.
        ("percent", "percentage", "0-100", "already"),
        ("100", "double", "twice", "inflat", "5000", "re-scal", "rescal",
         "multiply", "multiplying", "scaled twice"),
    ),
    "add_ignored_return_001": (
        # A correct diagnosis MUST recognise that failure is signalled by RETURN.
        ("return", "boolean", "bool", "false"),
        ("ignor", "not check", "unchecked", "never", "silent", "200", "success",
         "does not raise", "doesn't raise", "no exception"),
    ),
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower())


def satisfies_rubric(case_id: str, message: str, suggestion: str = "") -> bool:
    """True when the text mentions at least one term from every required group.

    Cases with no rubric default to True, so an unrubriced case degrades to
    positional-only matching rather than silently scoring zero.
    """
    groups = CONCEPT_RUBRIC.get(case_id)
    if not groups:
        return True
    blob = _norm(f"{message} {suggestion}")
    return all(any(term in blob for term in group) for group in groups)


def missing_groups(case_id: str, message: str, suggestion: str = "") -> list[tuple[str, ...]]:
    """Which concept groups the text failed to hit -- for failure analysis."""
    groups = CONCEPT_RUBRIC.get(case_id)
    if not groups:
        return []
    blob = _norm(f"{message} {suggestion}")
    return [g for g in groups if not any(term in blob for term in g)]
