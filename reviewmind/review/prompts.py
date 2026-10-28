"""Prompt construction for the LLM review arms.

CRITICAL EXPERIMENTAL CONSTRAINT
--------------------------------
Arms B (diff-only) and C (RAG) MUST share an identical system prompt, output
schema, persona and instruction set. The *only* permitted difference is the
presence of a retrieved-context block in the user message.

If the prompts differed in any other way, a measured difference between B and C
could be attributed to prompt wording rather than to retrieval, and RQ2 would be
unanswerable. `build_user_prompt` therefore takes `context_chunks` as an optional
argument to one shared function rather than having two separate builders.
"""

from __future__ import annotations

from typing import Sequence

from reviewmind.parsing.chunker import Chunk

# --------------------------------------------------------------------------- #
# System prompt -- identical for both LLM arms.
# --------------------------------------------------------------------------- #

SYSTEM_PROMPT = """\
You are a meticulous senior software engineer performing code review on a pull \
request. You review for correctness and safety, not style.

Priorities, highest first:
1. Logic errors that produce wrong results (off-by-one, inverted conditions,
   wrong arithmetic, incorrect return values).
2. Crash risks (dereferencing a value that may be None, unhandled exceptions,
   division by zero, index errors).
3. Security problems (injection, hardcoded credentials, unsafe deserialisation).
4. Missing or incorrect error handling.
5. Broken contracts -- a change that is locally valid but violates what the
   function's docstring promises or what its callers depend on.

Rules you must follow:
- Report ONLY problems you can justify from the code shown. Do not speculate.
- Every issue MUST cite the file and the specific line number where the problem is.
- Use line numbers from the NEW (post-change) version of the file.
- Do not report formatting, naming, type-annotation or import-ordering nits.
- If you genuinely find no defect, return an empty issues list. Reporting a
  non-issue to appear thorough is a failure, not caution.
- Be concise: one or two sentences per issue.

Respond with valid JSON only. No prose, no markdown fences. Schema:
{
  "issues": [
    {
      "file": "<path as shown in the diff>",
      "line": <integer line number in the new version>,
      "severity": "critical" | "high" | "medium" | "low",
      "category": "logic" | "crash" | "security" | "error_handling" | "contract",
      "message": "<what is wrong and why>",
      "suggestion": "<how to fix it>"
    }
  ],
  "test_suggestions": [
    {
      "target": "<function this test covers>",
      "name": "<test function name>",
      "rationale": "<what regression this would catch>",
      "code": "<pytest-style test body>"
    }
  ]
}"""


# --------------------------------------------------------------------------- #
# User prompt -- one builder, context optional.
# --------------------------------------------------------------------------- #

_DIFF_BLOCK = """\
## Pull request diff

The following change was proposed. Review it.

```diff
{diff}
```"""

_CONTEXT_BLOCK = """\

## Retrieved repository context

These are other parts of the repository related to the change, retrieved \
automatically. They are NOT part of the diff and are NOT under review. Use them \
to judge whether the change is correct in the wider codebase -- for example to \
check what a called function may return, or what a caller expects.

{chunks}"""

_TASK_BLOCK = """\

## Task

Review the diff above. Report defects as JSON per the schema. Also suggest tests \
that would catch the defects you found."""


def format_chunk(chunk: Chunk, index: int) -> str:
    lang = "python" if chunk.file.endswith(".py") else ""
    return (
        f"### [{index}] {chunk.file} lines {chunk.start_line}-{chunk.end_line}"
        f" ({chunk.kind} `{chunk.name}`)\n"
        f"```{lang}\n{chunk.text}\n```"
    )


def build_user_prompt(
    diff: str,
    context_chunks: Sequence[Chunk] | None = None,
) -> str:
    """Build the user message.

    `context_chunks=None` produces the Arm B (diff-only) prompt.
    A non-empty sequence produces the Arm C (RAG) prompt.
    Everything else is byte-identical between arms by construction.
    """
    parts = [_DIFF_BLOCK.format(diff=diff.strip())]

    if context_chunks:
        rendered = "\n\n".join(
            format_chunk(c, i) for i, c in enumerate(context_chunks, start=1)
        )
        parts.append(_CONTEXT_BLOCK.format(chunks=rendered))

    parts.append(_TASK_BLOCK)
    return "\n".join(parts)
