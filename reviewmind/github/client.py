"""GitHub API helpers: fetch PR diffs and post inline review comments."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from reviewmind.schema import Issue

SEVERITY_EMOJI = {
    "critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "info": "⚪",
}


@dataclass
class PostResult:
    posted_inline: int = 0
    posted_summary: bool = False
    skipped: int = 0
    errors: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


def format_comment(issue: Issue) -> str:
    emoji = SEVERITY_EMOJI.get(issue.severity, "⚪")
    parts = [f"{emoji} **{issue.severity.upper()}** · `{issue.category}`",
             "", issue.message]
    if issue.suggestion:
        parts += ["", f"**Suggestion:** {issue.suggestion}"]
    parts += ["", "<sub>Posted by ReviewMind — automated semantic review. "
                  "Not a substitute for human review.</sub>"]
    return "\n".join(parts)


def format_summary(issues: list[Issue], test_suggestions: list[Any],
                   model: str, retriever: str) -> str:
    if not issues:
        body = ["## ReviewMind", "", "No defects found in this change."]
    else:
        counts: dict[str, int] = {}
        for i in issues:
            counts[i.severity] = counts.get(i.severity, 0) + 1
        tally = " · ".join(
            f"{SEVERITY_EMOJI.get(s,'')} {n} {s}"
            for s, n in sorted(counts.items())
        )
        body = [f"## ReviewMind — {len(issues)} issue(s)", "", tally, ""]
        for i in issues:
            loc = f"`{i.file}:{i.line}`" if i.line else f"`{i.file}`"
            body.append(f"- {SEVERITY_EMOJI.get(i.severity,'')} {loc} — {i.message}")

    if test_suggestions:
        body += ["", f"### Suggested tests ({len(test_suggestions)})", ""]
        for t in test_suggestions[:5]:
            name = getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else "")
            why = getattr(t, "rationale", None) or (t.get("rationale") if isinstance(t, dict) else "")
            body.append(f"- `{name}` — {why}")

    body += ["", f"<sub>model: `{model}` · retrieval: `{retriever}`</sub>"]
    return "\n".join(body)


def post_review(
    repo_full_name: str,
    pr_number: int,
    token: str,
    issues: list[Issue],
    test_suggestions: Optional[list[Any]] = None,
    model: str = "",
    retriever: str = "",
    dry_run: bool = True,
) -> PostResult:
    """Post inline comments plus a summary.

    `dry_run=True` by default: posting to a pull request is outward-facing and
    visible to other people, so it is opt-in.

    Inline comments are attempted first; GitHub rejects a comment whose line is
    not part of the diff, so each failure is counted as `skipped` and the issue
    still appears in the summary rather than being lost.
    """
    result = PostResult()
    if dry_run:
        result.skipped = len(issues)
        return result

    try:
        from github import Github
    except ImportError:
        result.errors.append("PyGithub not installed")
        return result

    try:
        gh = Github(token)
        repo = gh.get_repo(repo_full_name)
        pr = repo.get_pull(pr_number)
        commit = repo.get_commit(pr.head.sha)
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"could not open PR: {type(exc).__name__}: {exc}")
        return result

    for issue in issues:
        if not issue.line or not issue.file:
            result.skipped += 1
            continue
        try:
            pr.create_review_comment(
                body=format_comment(issue), commit=commit,
                path=issue.file, line=int(issue.line),
            )
            result.posted_inline += 1
        except Exception:  # noqa: BLE001
            # Line not in the diff -- expected, not an error. It still appears
            # in the summary comment below.
            result.skipped += 1

    try:
        pr.create_issue_comment(
            format_summary(issues, test_suggestions or [], model, retriever))
        result.posted_summary = True
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"summary comment failed: {exc}")

    return result
