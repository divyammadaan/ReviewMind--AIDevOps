"""CLI for the issue -> PR automation (invoked by .github/workflows/reviewmind-fix.yml).

Reads the issue body from the ISSUE_BODY environment variable rather than argv,
because issue text is untrusted, arbitrarily long, and may contain shell
metacharacters. Passing it through the environment avoids any quoting or
injection concern in the workflow YAML.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from reviewmind.automation.issue_to_pr import (  # noqa: E402
    apply_and_open_pr, propose_patch,
)
from reviewmind.review.llm import build_llm  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate a PR that fixes an issue.")
    ap.add_argument("--issue-number", type=int, required=True)
    ap.add_argument("--issue-title", default="")
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--scoped-root", action="store_true",
                    help="the repo-root is already a restricted subdirectory, so "
                         "paths are relative to it (traversal, forbidden-path and "
                         ".py-only checks still apply)")
    ap.add_argument("--top-k", type=int, default=6)
    ap.add_argument("--apply", action="store_true",
                    help="actually create the branch and open the PR "
                         "(default is a dry run that writes nothing)")
    ap.add_argument("--output", default=None, help="write the proposal JSON here")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

    # Untrusted, possibly multi-line text: read from the environment.
    issue_body = os.getenv("ISSUE_BODY", "")
    issue_title = args.issue_title or os.getenv("ISSUE_TITLE", "")
    repo_root = Path(args.repo_root).resolve()

    proposal = propose_patch(
        issue_number=args.issue_number,
        issue_title=issue_title,
        issue_body=issue_body,
        repo_root=repo_root,
        llm=build_llm(),
        top_k=args.top_k,
        scoped_root=args.scoped_root,
    )

    print(json.dumps(proposal.to_dict(), indent=2))

    if args.output:
        Path(args.output).write_text(json.dumps(proposal.to_dict(), indent=2))

    rejected = [c for c in proposal.changes if not c.accepted]
    if rejected:
        print("\nREJECTED CHANGES (safety gate):", file=sys.stderr)
        for c in rejected:
            print(f"  {c.path}: {c.rejected_reason}", file=sys.stderr)

    if not proposal.accepted_changes:
        print("\nNo acceptable changes were produced. "
              "This is a valid outcome, not a crash.", file=sys.stderr)
        return 0 if not proposal.errors else 1

    result = apply_and_open_pr(
        proposal=proposal,
        repo_root=repo_root,
        repo_full_name=os.getenv("REPO_FULL_NAME", ""),
        token=os.getenv("GITHUB_TOKEN", ""),
        base_branch=os.getenv("BASE_BRANCH", "main"),
        dry_run=not args.apply,
    )
    print("\n" + json.dumps(result, indent=2))
    return 1 if result.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
