"""A1 deliverable: Sourcegraph semantic code navigation and query formulation.

Runs a documented suite of navigation queries against Sourcegraph and records
both the queries and their results, so the navigation capability is evidenced
rather than asserted.

Each query demonstrates a DIFFERENT navigation capability, and each is annotated
with what it demonstrates and why a plain text search could not answer it.

Usage:
    .venv/bin/python experiments/sourcegraph_navigation.py
    .venv/bin/python experiments/sourcegraph_navigation.py --output results/sourcegraph.json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from reviewmind.navigation.sourcegraph import (  # noqa: E402
    SourcegraphClient, SourcegraphUnavailable,
)

FLASK = r"^github\.com/pallets/flask$"
WERKZEUG = r"^github\.com/pallets/werkzeug$"
REQUESTS = r"^github\.com/psf/requests$"


@dataclass
class NavQuery:
    label: str
    capability: str          # which navigation capability this exercises
    query: str
    why_not_grep: str        # why plain text search cannot answer this


QUERIES: list[NavQuery] = [
    NavQuery(
        label="symbol definition lookup",
        capability="type:symbol -- index-backed symbol search",
        query=f"repo:{WERKZEUG} lang:python type:symbol cached_property",
        why_not_grep=(
            "type:symbol queries Sourcegraph's symbol index, returning "
            "DEFINITIONS rather than every textual mention. grep would also "
            "return imports, call sites and comments."
        ),
    ),
    NavQuery(
        label="structural search (syntax-aware)",
        capability="patterntype:structural -- matches code SHAPE, not text",
        query=f"repo:{FLASK} lang:python patterntype:structural except ...: pass",
        why_not_grep=(
            "Structural search matches the syntactic shape regardless of "
            "whitespace, the exception type, or intervening comments. A regex "
            "for this is either unreadable or wrong."
        ),
    ),
    NavQuery(
        label="call-site navigation",
        capability="scoped literal search -- approximate 'find references'",
        query=f"repo:{FLASK} lang:python send_file(",
        why_not_grep=(
            "Scoped to one repo and one language by the index, so results are "
            "not polluted by unrelated languages or vendored copies."
        ),
    ),
    NavQuery(
        label="cross-repository pattern search",
        capability="repo-unscoped search across ALL indexed public repos",
        query="lang:python patterntype:structural subprocess.run(..., shell=True)",
        why_not_grep=(
            "THE capability our local retriever fundamentally cannot provide: "
            "searching code we have not cloned. Finds a risky pattern across "
            "the whole public corpus."
        ),
    ),
    NavQuery(
        label="diff/history search",
        capability="type:diff -- searches commit history, not the working tree",
        query=f"repo:{REQUESTS} type:diff lang:python verify=False",
        why_not_grep=(
            "Searches what CHANGED over history. grep sees only the current "
            "checkout, so a pattern added and later removed is invisible to it."
        ),
    ),
    NavQuery(
        label="filename-scoped search",
        capability="file: filters -- narrow by path",
        query=f"repo:{FLASK} file:test_ lang:python patterntype:structural assert ...",
        why_not_grep=(
            "Combines a path filter with a structural pattern in one indexed "
            "query."
        ),
    ),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="results/sourcegraph_navigation.json")
    ap.add_argument("--count", type=int, default=3)
    args = ap.parse_args()

    try:
        client = SourcegraphClient()
    except SourcegraphUnavailable as exc:
        print(f"Sourcegraph unavailable: {exc}", file=sys.stderr)
        return 1

    print("=" * 74)
    print("SOURCEGRAPH SEMANTIC CODE NAVIGATION -- A1 deliverable")
    print("=" * 74)
    print(f"src CLI:  {client.version()}")
    print(f"endpoint: {client.endpoint}")
    print(f"auth:     {'token' if client.access_token else 'unauthenticated (public code)'}")
    print()

    records = []
    for i, nq in enumerate(QUERIES, start=1):
        print("-" * 74)
        print(f"[{i}] {nq.label}")
        print(f"    capability: {nq.capability}")
        print(f"    query: {nq.query}")
        res = client.search(nq.query, count=args.count)
        print(f"    -> {len(res.hits)} hits  errors={res.errors}")
        for h in res.hits[:args.count]:
            preview = " ".join(h.preview.split())[:66]
            print(f"       {h.location()}")
            if preview:
                print(f"         | {preview}")
        print(f"    why not grep: {nq.why_not_grep}")
        print()

        records.append({
            "label": nq.label,
            "capability": nq.capability,
            "query": nq.query,
            "why_not_grep": nq.why_not_grep,
            "n_hits": len(res.hits),
            "errors": res.errors,
            "hits": [
                {"repository": h.repository, "path": h.path,
                 "line_number": h.line_number, "preview": h.preview[:160]}
                for h in res.hits[:args.count]
            ],
        })

    out = ROOT / args.output
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "src_version": client.version(),
        "endpoint": client.endpoint,
        "authenticated": bool(client.access_token),
        "n_queries": len(records),
        "n_queries_with_hits": sum(1 for r in records if r["n_hits"] > 0),
        "queries": records,
    }, indent=2))

    hit = sum(1 for r in records if r["n_hits"] > 0)
    print("=" * 74)
    print(f"{hit}/{len(records)} queries returned hits")
    print(f"wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
