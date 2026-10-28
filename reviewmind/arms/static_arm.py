"""Arm A -- conventional static analysis baseline (ruff + bandit).

This is the non-AI reference point for RQ1. It must be a FAIR baseline, which
drives two deliberate choices:

  1. We enable ruff's bug-oriented rule families (E/F/B/S/SIM/RET), not just the
     default E/F. Running ruff in a deliberately weak configuration and then
     declaring the LLM superior would be a rigged comparison.
  2. bandit runs at low confidence/severity thresholds so it reports everything
     it can. This hurts its precision, which we acknowledge rather than hide --
     a security scanner tuned to silence would understate the baseline's recall.

Both tools analyse code statically. Neither executes it. This preserves
ReviewMind's read-only safety property for the baseline too.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from reviewmind.schema import Issue, ReviewResult

# Bug-oriented rule families. Chosen to give the baseline its best honest shot.
RUFF_SELECT = "E,F,B,S,SIM,RET,ARG,TRY"

_BANDIT_SEVERITY = {"LOW": "low", "MEDIUM": "high", "HIGH": "critical"}


def _venv_bin(tool: str) -> str | None:
    """Prefer the tool from our own venv so results are reproducible."""
    local = Path(sys.executable).parent / tool
    if local.exists():
        return str(local)
    return shutil.which(tool)


def run_ruff(target_dir: Path, changed_files: list[str] | None = None) -> list[Issue]:
    exe = _venv_bin("ruff")
    if exe is None:
        return []

    target_dir = target_dir.resolve()
    paths = [str(target_dir / f) for f in changed_files] if changed_files else [str(target_dir)]
    proc = subprocess.run(
        [exe, "check", "--output-format", "json", "--select", RUFF_SELECT,
         "--no-cache", "--force-exclude", *paths],
        capture_output=True, text=True, cwd=str(target_dir),
    )

    try:
        payload = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return []

    issues: list[Issue] = []
    for item in payload:
        code = item.get("code") or ""
        issues.append(Issue(
            file=item.get("filename", ""),
            line=(item.get("location") or {}).get("row"),
            # ruff does not grade severity; S* are security rules, rest are "medium".
            severity="high" if code.startswith("S") else "medium",
            category="security" if code.startswith("S") else "lint",
            message=item.get("message", ""),
            suggestion=((item.get("fix") or {}) or {}).get("message", "") or "",
            source="ruff",
            rule_id=code,
        ))
    return issues


def run_bandit(target_dir: Path, changed_files: list[str] | None = None) -> list[Issue]:
    exe = _venv_bin("bandit")
    if exe is None:
        return []

    target_dir = target_dir.resolve()
    if changed_files:
        paths = [str(target_dir / f) for f in changed_files]
        cmd = [exe, "-f", "json", "-ll", "-ii", *paths]
    else:
        cmd = [exe, "-r", "-f", "json", "-ll", "-ii", str(target_dir)]

    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(target_dir))
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return []

    issues: list[Issue] = []
    for item in payload.get("results", []):
        issues.append(Issue(
            file=item.get("filename", ""),
            line=item.get("line_number"),
            severity=_BANDIT_SEVERITY.get(item.get("issue_severity", "LOW"), "low"),
            category="security",
            message=item.get("issue_text", ""),
            suggestion="",
            source="bandit",
            rule_id=item.get("test_id", ""),
        ))
    return issues


def review(case_id: str, repo_dir: Path, changed_files: list[str]) -> ReviewResult:
    """Run the static baseline over the changed files of one case."""
    started = time.perf_counter()
    result = ReviewResult(arm="static", case_id=case_id, model="n/a (static analysis)",
                          retriever="n/a")
    try:
        result.issues.extend(run_ruff(repo_dir, changed_files))
        result.issues.extend(run_bandit(repo_dir, changed_files))
    except (OSError, subprocess.SubprocessError) as exc:
        result.errors.append(f"static analysis failed: {exc}")

    result.latency_s = time.perf_counter() - started
    return result
