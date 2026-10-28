"""Entry point invoked by .github/workflows/reviewmind.yml.

Reads a unified diff from a file, reviews it with retrieval, and posts the
result to the pull request.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from reviewmind.arms import llm_arm  # noqa: E402
from reviewmind.github.client import post_review  # noqa: E402
from reviewmind.parsing.chunker import chunk_repo  # noqa: E402
from reviewmind.retrieval.graph import GraphRetriever  # noqa: E402
from reviewmind.review.llm import build_llm  # noqa: E402


def changed_file_from_diff(diff: str) -> str | None:
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            return line[len("+++ b/"):].strip()
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--diff", required=True, help="path to a unified diff file")
    ap.add_argument("--repo", default="", help="owner/name")
    ap.add_argument("--pr", type=int, default=0)
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--top-k", type=int, default=6)
    ap.add_argument("--output", default=None)
    ap.add_argument("--no-post", action="store_true",
                    help="review only; do not write anything to the PR")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

    diff_path = Path(args.diff)
    if not diff_path.exists():
        print(f"diff not found: {diff_path}", file=sys.stderr)
        return 1
    diff = diff_path.read_text(encoding="utf-8", errors="replace")
    if not diff.strip():
        print("empty diff -- nothing to review")
        return 0

    repo_root = Path(args.repo_root).resolve()
    retriever = GraphRetriever(chunk_repo(repo_root))

    result = llm_arm.review(
        case_id=f"pr-{args.pr}", diff=diff, llm=build_llm(),
        retriever=retriever, repo_dir=repo_root,
        changed_file=changed_file_from_diff(diff), top_k=args.top_k,
    )

    payload = result.to_dict()
    print(json.dumps(payload, indent=2))
    if args.output:
        Path(args.output).write_text(json.dumps(payload, indent=2))

    import os
    token = os.getenv("GITHUB_TOKEN", "")
    if args.no_post or not token or not args.repo or not args.pr:
        print("\n(not posting: --no-post, or missing token/repo/pr)", file=sys.stderr)
        return 0

    posted = post_review(
        repo_full_name=args.repo, pr_number=args.pr, token=token,
        issues=result.issues, test_suggestions=result.test_suggestions,
        model=result.model, retriever=result.retriever, dry_run=False,
    )
    print(f"\nposted inline={posted.posted_inline} summary={posted.posted_summary} "
          f"skipped={posted.skipped} errors={posted.errors}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
