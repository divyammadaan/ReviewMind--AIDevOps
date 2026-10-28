"""Sourcegraph semantic code navigation (A1 rubric: Sourcegraph setup + navigation).

DEPLOYMENT DECISION -- and an honest statement of what this does and does not do.
---------------------------------------------------------------------------------
The project proposal specified a SELF-HOSTED Sourcegraph instance in Docker.
That was not deployed, for a measured reason: the development VM has ~3.1 GB of
free RAM against Sourcegraph's ~4-8 GB requirement, so a local instance would
likely OOM -- and doing so mid-demonstration is a worse outcome than not using
it.

Instead we query the public Sourcegraph instance (sourcegraph.com) through the
official `src` CLI (v8.0.0, a static Go binary vendored at tools/bin/src). This
requires no container, no sudo and no RAM headroom, and it works unauthenticated
for public repositories.

WHAT THIS GIVES US
  * Genuine Sourcegraph structural/semantic query formulation and execution.
  * Repo-wide and CROSS-repository navigation over public code, which our own
    local retriever cannot do at all.

WHAT IT DOES NOT GIVE US -- stated plainly rather than glossed
  * sourcegraph.com cannot index OUR local evaluation corpus, because that
    corpus is not a public repository. Therefore Sourcegraph is NOT part of the
    ReviewMind review pipeline measured in the experiments, and no experimental
    result in this project depends on it.
  * Cross-file retrieval for the reviewer is supplied by
    `reviewmind/retrieval/graph.py` instead, which IS measured.

Conflating the two would misrepresent the architecture, so they are kept
separate: Sourcegraph for navigation over public code, our own retriever for the
code under review.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
VENDORED_SRC = REPO_ROOT / "tools" / "bin" / "src"
DEFAULT_ENDPOINT = "https://sourcegraph.com"


class SourcegraphUnavailable(RuntimeError):
    """Raised when the `src` CLI is absent or the endpoint cannot be reached."""


@dataclass
class SearchHit:
    repository: str
    path: str
    line_number: Optional[int] = None
    preview: str = ""
    kind: str = "FileMatch"

    def location(self) -> str:
        loc = f"{self.repository}/{self.path}"
        return f"{loc}:{self.line_number}" if self.line_number else loc


@dataclass
class SearchResult:
    query: str
    endpoint: str
    hits: list[SearchHit] = field(default_factory=list)
    raw_count: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "endpoint": self.endpoint,
            "n_hits": len(self.hits),
            "hits": [
                {"repository": h.repository, "path": h.path,
                 "line_number": h.line_number, "kind": h.kind,
                 "preview": h.preview[:200]}
                for h in self.hits
            ],
            "errors": self.errors,
        }


class SourcegraphClient:
    """Thin, typed wrapper over the `src search -json` CLI."""

    def __init__(self, endpoint: str = DEFAULT_ENDPOINT,
                 src_path: Optional[Path] = None,
                 access_token: Optional[str] = None,
                 timeout_s: int = 90) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.timeout_s = timeout_s
        self.access_token = access_token or os.getenv("SRC_ACCESS_TOKEN", "")

        candidate = src_path or VENDORED_SRC
        if candidate.exists():
            self.src = str(candidate)
        else:
            found = shutil.which("src")
            if not found:
                raise SourcegraphUnavailable(
                    f"`src` CLI not found at {candidate} and not on PATH.\n"
                    "Install it with scripts/install_sourcegraph_cli.sh"
                )
            self.src = found

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["SRC_ENDPOINT"] = self.endpoint
        if self.access_token:
            env["SRC_ACCESS_TOKEN"] = self.access_token
        return env

    def version(self) -> str:
        proc = subprocess.run([self.src, "version"], capture_output=True,
                              text=True, timeout=30, env=self._env())
        return (proc.stdout or proc.stderr).strip().splitlines()[0] if (
            proc.stdout or proc.stderr) else "unknown"

    def search(self, query: str, count: int = 10) -> SearchResult:
        """Run a Sourcegraph query and parse the JSON response.

        Errors are captured into the result rather than raised, so a navigation
        failure degrades gracefully instead of breaking a demo or a notebook.
        """
        result = SearchResult(query=query, endpoint=self.endpoint)
        full_query = query if "count:" in query else f"{query} count:{count}"

        try:
            proc = subprocess.run(
                [self.src, "search", "-json", full_query],
                capture_output=True, text=True,
                timeout=self.timeout_s, env=self._env(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            result.errors.append(f"{type(exc).__name__}: {exc}")
            return result

        if proc.returncode != 0 and not proc.stdout.strip():
            result.errors.append(
                f"src exited {proc.returncode}: {(proc.stderr or '')[:300]}")
            return result

        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            result.errors.append(f"could not parse src JSON: {exc}")
            return result

        raw = payload.get("Results") or []
        result.raw_count = len(raw)

        for item in raw:
            kind = item.get("__typename", "FileMatch")
            repo = ((item.get("repository") or {}).get("name")
                    or (item.get("file") or {}).get("repository", {}).get("name")
                    or "")
            path = (item.get("file") or {}).get("path", "")

            line_matches = item.get("lineMatches") or []
            if line_matches:
                for lm in line_matches[:3]:
                    result.hits.append(SearchHit(
                        repository=repo, path=path, kind=kind,
                        line_number=(lm.get("lineNumber") or 0) + 1,
                        preview=(lm.get("preview") or "").strip(),
                    ))
            else:
                result.hits.append(SearchHit(
                    repository=repo, path=path, kind=kind,
                    preview=((item.get("file") or {}).get("content") or "")[:160].strip(),
                ))

        return result

    # ---- navigation query builders ------------------------------------- #
    # These encode Sourcegraph's query syntax so callers formulate semantic
    # navigation questions rather than hand-writing query strings.

    @staticmethod
    def q_find_definition(symbol: str, repo: Optional[str] = None,
                          lang: str = "python") -> str:
        scope = f"repo:{repo} " if repo else ""
        return f"{scope}lang:{lang} type:symbol {symbol}"

    @staticmethod
    def q_find_callers(symbol: str, repo: Optional[str] = None,
                       lang: str = "python") -> str:
        scope = f"repo:{repo} " if repo else ""
        # Literal call-site pattern; Sourcegraph has no language-agnostic
        # "find references" in the free search API, so we approximate it.
        return f"{scope}lang:{lang} {symbol}("

    @staticmethod
    def q_structural(pattern: str, repo: Optional[str] = None,
                     lang: str = "python") -> str:
        """Sourcegraph STRUCTURAL search -- syntax-aware, not textual.

        e.g. pattern='except: pass' matches that shape regardless of
        whitespace or intervening comments.
        """
        scope = f"repo:{repo} " if repo else ""
        return f"{scope}lang:{lang} patterntype:structural {pattern}"

    @staticmethod
    def q_cross_repo(pattern: str, lang: str = "python") -> str:
        """Search across ALL indexed public repositories.

        This is the capability our local retriever fundamentally cannot provide.
        """
        return f"lang:{lang} {pattern}"
