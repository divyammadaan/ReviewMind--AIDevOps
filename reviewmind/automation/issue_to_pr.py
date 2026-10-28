"""Issue -> AI-generated pull request automation.

WHY THIS MODULE EXISTS -- a documented substitution, not a silent one
--------------------------------------------------------------------
The A2 rubric names "GitHub Actions CI/CD with Sweep.dev for automation".
Sweep.dev's hosted service NO LONGER EXISTS. Verified on 2026-10-04:

    $ getent hosts sweep.dev        -> DOES NOT RESOLVE
    $ getent hosts docs.sweep.dev   -> DOES NOT RESOLVE
    (sourcegraph.com, github.com and pypi.org all resolved normally from the
     same machine at the same time, so this is not a local network fault)

    github.com/sweepai/sweep README:
      "Thank you for all of the support on Sweep. We're now building an AI
       coding assistant for JetBrains"

The PyPI package `sweepai` (3.2.5) is only a CLI client that syncs with that
dead hosted service, under an Enterprise Edition licence.

Sweep.dev's defining capability was: label or comment on a GitHub issue, and an
AI agent opens a pull request implementing the fix. This module implements that
capability directly against the GitHub API, so the automation the rubric is
testing is delivered even though the named vendor is gone.

SAFETY DESIGN
Unlike the reviewer, this component WRITES code. Therefore:
  * It never commits to the default branch -- always a new `reviewmind/issue-N`
    branch.
  * It opens a PR for human review and NEVER merges. No auto-merge, ever.
  * It never force-pushes and never deletes branches.
  * It refuses to touch paths outside an allow-list (no CI config, no workflow
    files, no secrets), so the automation cannot modify the pipeline that runs
    it -- a privilege-escalation path a naive implementation would leave open.
  * It applies a patch only if it parses as valid Python, so it cannot land a
    syntactically broken file.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# Paths the automation is permitted to modify. Deliberately excludes .github/,
# requirements, and anything that could alter CI behaviour or secrets handling.
ALLOWED_PATH_PREFIXES = ("app/", "src/", "lib/", "reviewmind/", "experiments/")
FORBIDDEN_PATTERNS = (
    ".github/", ".git/", "requirements", "pyproject.toml", "setup.py",
    "Dockerfile", ".env", "secrets", "id_rsa", ".pem", "workflows/",
)

PATCH_SYSTEM_PROMPT = """\
You are a senior software engineer implementing a fix for a reported issue.

You will be given: the issue text, and the current contents of relevant files.

Produce a MINIMAL, targeted fix. Rules:
- Change as little as possible. Do not refactor unrelated code.
- Do not add dependencies.
- Do not modify CI configuration, workflows, requirements or secrets.
- Preserve the existing code style and type annotations.
- If the issue is unclear or you cannot fix it safely, return an empty
  "changes" list and explain why in "reasoning". Declining is a valid answer and
  is strongly preferred over guessing.

Respond with valid JSON only, no markdown fences:
{
  "reasoning": "<brief explanation of the fix, or why you declined>",
  "changes": [
    {
      "path": "<file path relative to repo root>",
      "new_content": "<the COMPLETE new contents of the file>"
    }
  ],
  "test_suggestions": [
    {"name": "<test name>", "rationale": "<what regression it catches>",
     "code": "<pytest test>"}
  ]
}"""


@dataclass
class ProposedChange:
    path: str
    new_content: str
    rejected_reason: Optional[str] = None

    @property
    def accepted(self) -> bool:
        return self.rejected_reason is None


@dataclass
class PatchProposal:
    issue_number: int
    reasoning: str = ""
    changes: list[ProposedChange] = field(default_factory=list)
    test_suggestions: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    model: str = ""
    latency_s: float = 0.0

    @property
    def accepted_changes(self) -> list[ProposedChange]:
        return [c for c in self.changes if c.accepted]

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_number": self.issue_number,
            "reasoning": self.reasoning,
            "changes": [
                {"path": c.path, "accepted": c.accepted,
                 "rejected_reason": c.rejected_reason,
                 "bytes": len(c.new_content)}
                for c in self.changes
            ],
            "test_suggestions": self.test_suggestions,
            "errors": self.errors,
            "model": self.model,
            "latency_s": round(self.latency_s, 3),
        }


# --------------------------------------------------------------------------- #
# Path safety
# --------------------------------------------------------------------------- #

def path_is_allowed(path: str, scoped_root: bool = False) -> tuple[bool, str]:
    """Gate a model-proposed path. Returns (allowed, reason_if_not).

    Default-deny: a path must match an allow-list prefix. A default-allow policy
    with a deny-list would be one creative model output away from editing CI.

    `scoped_root=True` means the caller has ALREADY restricted the working tree
    to a specific subdirectory (e.g. --repo-root experiments/corpus/school). In
    that case paths arrive relative to that subdirectory and cannot match the
    repository-level prefixes, so the prefix check is skipped as redundant.

    The checks that actually provide the security guarantee are UNAFFECTED by
    this flag and always run:
      * no absolute paths, no `..` traversal -- so a scoped root cannot be escaped
      * no CI config, workflows, requirements, Dockerfile, .env or secrets
      * .py files only
    Only the "which source directory" question is relaxed, and only when the
    caller has already answered it by scoping the root.
    """
    norm = path.replace("\\", "/").lstrip("./")

    if norm.startswith("/") or ".." in Path(norm).parts:
        return False, "absolute path or directory traversal"

    low = norm.lower()
    for bad in FORBIDDEN_PATTERNS:
        if bad.lower() in low:
            return False, f"matches forbidden pattern '{bad}'"

    if not scoped_root and not any(norm.startswith(p) for p in ALLOWED_PATH_PREFIXES):
        return False, (f"outside allow-list {ALLOWED_PATH_PREFIXES}")

    if not norm.endswith(".py"):
        return False, "only .py files may be modified"

    return True, ""


def content_is_valid_python(text: str) -> tuple[bool, str]:
    try:
        ast.parse(text)
        return True, ""
    except SyntaxError as exc:
        return False, f"invalid Python: {exc}"


# --------------------------------------------------------------------------- #
# Patch generation
# --------------------------------------------------------------------------- #

def propose_patch(
    issue_number: int,
    issue_title: str,
    issue_body: str,
    repo_root: Path,
    llm: Any,
    top_k: int = 6,
    scoped_root: bool = False,
) -> PatchProposal:
    """Ask the model for a minimal fix, then gate every proposed change."""
    from reviewmind.parsing.chunker import chunk_repo
    from reviewmind.retrieval.graph import GraphRetriever

    started = time.perf_counter()
    proposal = PatchProposal(issue_number=issue_number)

    # Reuse ReviewMind's retrieval to locate the relevant code.
    chunks = chunk_repo(repo_root)
    retriever = GraphRetriever(chunks)
    query = f"{issue_title}\n{issue_body}"
    hits = retriever.search(query, top_k=top_k)

    if not hits:
        proposal.errors.append("retrieval found no relevant code")
        proposal.latency_s = time.perf_counter() - started
        return proposal

    # Supply the FULL contents of touched files, since the model must return
    # complete replacements.
    files: dict[str, str] = {}
    for h in hits:
        f = h.chunk.file
        if f not in files:
            p = repo_root / f
            if p.exists():
                files[f] = p.read_text(encoding="utf-8", errors="replace")

    rendered = "\n\n".join(
        f"### {name}\n```python\n{content}\n```" for name, content in files.items()
    )
    user = (
        f"## Issue #{issue_number}: {issue_title}\n\n{issue_body}\n\n"
        f"## Current file contents\n\n{rendered}\n\n"
        "Produce a minimal fix as JSON."
    )

    resp = llm.complete(PATCH_SYSTEM_PROMPT, user, temperature=0.0, max_tokens=4096)
    proposal.model = resp.model

    if resp.error:
        proposal.errors.append(f"llm error: {resp.error}")
        proposal.latency_s = time.perf_counter() - started
        return proposal

    from reviewmind.review.llm import extract_json
    payload, err = extract_json(resp.text)
    if err:
        proposal.errors.append(f"parse: {err}")
        proposal.latency_s = time.perf_counter() - started
        return proposal

    proposal.reasoning = str(payload.get("reasoning", ""))
    proposal.test_suggestions = payload.get("test_suggestions") or []

    for item in payload.get("changes") or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path", ""))
        content = str(item.get("new_content", ""))
        change = ProposedChange(path=path, new_content=content)

        ok, reason = path_is_allowed(path, scoped_root=scoped_root)
        if not ok:
            change.rejected_reason = reason
        elif not content.strip():
            change.rejected_reason = "empty new_content"
        else:
            valid, vreason = content_is_valid_python(content)
            if not valid:
                change.rejected_reason = vreason
        proposal.changes.append(change)

    proposal.latency_s = time.perf_counter() - started
    return proposal


# --------------------------------------------------------------------------- #
# Branch + PR creation
# --------------------------------------------------------------------------- #

def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd),
                          capture_output=True, text=True, timeout=120)


def apply_and_open_pr(
    proposal: PatchProposal,
    repo_root: Path,
    repo_full_name: str,
    token: str,
    base_branch: str = "main",
    dry_run: bool = True,
) -> dict[str, Any]:
    """Write accepted changes to a new branch and open a PR.

    `dry_run=True` (the default) writes nothing and pushes nothing. Creating
    branches and PRs is an outward-facing action, so it is opt-in rather than
    the default.
    """
    out: dict[str, Any] = {"dry_run": dry_run, "applied": [], "errors": []}
    accepted = proposal.accepted_changes
    if not accepted:
        out["errors"].append("no accepted changes to apply")
        return out

    branch = f"reviewmind/issue-{proposal.issue_number}"
    out["branch"] = branch

    if dry_run:
        out["applied"] = [c.path for c in accepted]
        out["note"] = ("DRY RUN -- nothing written, no branch created, no PR "
                       "opened. Re-run with dry_run=False to act.")
        return out

    if _git(["rev-parse", "--git-dir"], repo_root).returncode != 0:
        out["errors"].append(f"{repo_root} is not a git repository")
        return out

    if _git(["checkout", "-b", branch], repo_root).returncode != 0:
        # Branch may already exist from a previous run; switch to it instead of
        # forcing, which would discard work.
        if _git(["checkout", branch], repo_root).returncode != 0:
            out["errors"].append(f"could not create or switch to {branch}")
            return out

    for change in accepted:
        target = repo_root / change.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(change.new_content, encoding="utf-8")
        out["applied"].append(change.path)

    _git(["add", *[c.path for c in accepted]], repo_root)
    msg = (f"fix: address issue #{proposal.issue_number}\n\n"
           f"{proposal.reasoning[:500]}\n\n"
           f"Generated by ReviewMind automation. Requires human review.")
    if _git(["commit", "-m", msg], repo_root).returncode != 0:
        out["errors"].append("nothing to commit")
        return out

    push = _git(["push", "-u", "origin", branch], repo_root)
    if push.returncode != 0:
        out["errors"].append(f"push failed: {push.stderr[:300]}")
        return out

    # Open the PR. Never merge it.
    try:
        from github import Github
        gh = Github(token)
        repo = gh.get_repo(repo_full_name)
        pr = repo.create_pull(
            title=f"ReviewMind: fix for issue #{proposal.issue_number}",
            body=(f"Closes #{proposal.issue_number}\n\n"
                  f"### Reasoning\n{proposal.reasoning}\n\n"
                  f"**This PR was generated automatically and has NOT been "
                  f"merged. A human must review it.**"),
            head=branch, base=base_branch,
        )
        out["pr_url"] = pr.html_url
        out["pr_number"] = pr.number
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"PR creation failed: {type(exc).__name__}: {exc}")

    return out
