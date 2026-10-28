#!/usr/bin/env bash
# Ask ReviewMind to WRITE a fix for a described problem.
#
#   ./scripts/fix_issue.sh "the /badge endpoint crashes for missing students"
#   ./scripts/fix_issue.sh "..." --apply        # actually write the files
#
# By default this is a DRY RUN: it generates the fix, shows you the diff, and
# writes nothing. Editing source is an action with consequences, so it is
# opt-in rather than the default.
#
# ReviewMind's REVIEW path remains strictly read-only. This is a separate,
# explicitly-invoked capability, with a default-deny path allow-list that
# forbids touching CI config, workflows, requirements, Dockerfile or .env.
set -euo pipefail

DESC="${1:?usage: ./scripts/fix_issue.sh \"description of the problem\" [--apply]}"
APPLY="${2:-}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${REVIEWMIND_TARGET:-$ROOT/experiments/corpus/school}"
cd "$ROOT"

echo "──────────────────────────────────────────────"
echo " ReviewMind — generate a fix"
echo " target : $TARGET"
echo " issue  : $DESC"
echo "──────────────────────────────────────────────"

BACKUP="$(mktemp -d)"
cp -r "$TARGET"/*.py "$BACKUP"/ 2>/dev/null || true

ISSUE_BODY="$DESC" .venv/bin/python -m reviewmind.automation.cli \
  --issue-number 0 \
  --issue-title "$DESC" \
  --repo-root "$TARGET" \
  --scoped-root \
  --output /tmp/reviewmind_fix.json > /tmp/reviewmind_fix.log 2>&1 || true

.venv/bin/python - "$TARGET" "$APPLY" "$BACKUP" <<'PYEOF'
import json, shutil, subprocess, sys
from pathlib import Path

target, apply_flag, backup = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
try:
    d = json.loads(Path("/tmp/reviewmind_fix.json").read_text())
except Exception:
    print("  could not generate a fix -- see /tmp/reviewmind_fix.log"); raise SystemExit(1)

print(f"\n  Reasoning: {d['reasoning'][:200]}\n")
accepted = [c for c in d["changes"] if c["accepted"]]
rejected = [c for c in d["changes"] if not c["accepted"]]

for c in rejected:
    print(f"  REJECTED {c['path']}: {c['rejected_reason']}")
if not accepted:
    print("  No acceptable fix was produced. That is a valid outcome.")
    raise SystemExit(0)

# Re-generate to obtain the file contents (the summary JSON omits them).
sys.path.insert(0, ".")
from dotenv import load_dotenv; load_dotenv(".env")
from reviewmind.automation.issue_to_pr import propose_patch
from reviewmind.review.llm import build_llm

prop = propose_patch(0, d["reasoning"][:120], d["reasoning"], target,
                     build_llm(), scoped_root=True)

for c in prop.accepted_changes:
    path = target / c.path
    old = path.read_text() if path.exists() else ""
    print(f"  --- proposed change to {c.path} ---")
    import difflib
    for line in difflib.unified_diff(old.splitlines(), c.new_content.splitlines(),
                                     lineterm="", n=2):
        if line.startswith(("+++", "---")):
            continue
        print("   " + line)
    if apply_flag == "--apply":
        path.write_text(c.new_content)
        print(f"\n  APPLIED to {path}")
    else:
        print(f"\n  DRY RUN -- nothing written. Re-run with --apply to write it.")

for t in prop.test_suggestions[:2]:
    print(f"\n  Suggested test: {t.get('name')}")
PYEOF

echo "──────────────────────────────────────────────"
