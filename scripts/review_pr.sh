#!/usr/bin/env bash
# Review a pull request and post the results to GitHub.
#
#   ./scripts/review_pr.sh 1
#
# Fetches the PR diff, runs ReviewMind (retrieval + LLM), and posts an inline
# comment plus a summary. Intended for a live demonstration: one command, then
# refresh the PR in the browser.
set -euo pipefail

PR="${1:?usage: ./scripts/review_pr.sh <pr-number> [repo-root]}"
# Optional second argument: which directory to index for retrieval.
# Indexing a whole large repository can exceed the model's per-minute token
# budget, so point this at the project under review when it is a subdirectory.
REPO_ROOT_ARG="${2:-.}"
REPO="${REVIEWMIND_REPO:-Deadmaus07/ReviewMind}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ ! -f "$HOME/.rm_token" ]; then
  echo "ERROR: no GitHub token at ~/.rm_token" >&2; exit 1
fi
TOKEN="$(cat "$HOME/.rm_token")"

echo "──────────────────────────────────────────────"
echo " ReviewMind  →  $REPO  PR #$PR   (indexing: $REPO_ROOT_ARG)"
echo "──────────────────────────────────────────────"

echo "[1/3] fetching the diff from GitHub..."
curl -sf -H "Authorization: Bearer $TOKEN" \
     -H "Accept: application/vnd.github.v3.diff" \
     "https://api.github.com/repos/$REPO/pulls/$PR" > /tmp/reviewmind_pr.diff
echo "      $(grep -c '^+' /tmp/reviewmind_pr.diff || true) added lines"

echo "[2/3] reviewing (retrieval + LLM)..."
echo "[3/3] posting to GitHub..."
GITHUB_TOKEN="$TOKEN" .venv/bin/python -m reviewmind.github.action \
  --diff /tmp/reviewmind_pr.diff \
  --repo "$REPO" --pr "$PR" --repo-root "$REPO_ROOT_ARG" \
  --output /tmp/reviewmind_review.json 2>/dev/null | \
  .venv/bin/python -c "
import json,sys
d=json.load(sys.stdin)
print()
print(f\"  {len(d['issues'])} issue(s) found in {d['latency_s']}s\")
for i in d['issues']:
    print(f\"    [{i['severity'].upper()}] {i['file']}:{i['line']}\")
    print(f\"      {i['message'][:100]}\")
for t in d['test_suggestions']:
    print(f\"    [TEST] {t['name']}\")
print()
print(f\"  model={d['model']}  retrieval={d['retriever']}\")
"

echo "──────────────────────────────────────────────"
echo " Posted. Refresh:"
echo " https://github.com/$REPO/pull/$PR"
echo "──────────────────────────────────────────────"
