"""Retrieval ablation: context-recall@k for the three retrieval strategies.

Answers a sub-question of RQ2 that is separable from LLM behaviour:
*before* any model is involved, does the retriever actually put the
defect-relevant chunk in the context window?

This is measured on the 4 cross-file cases, whose critical chunk is known by
construction (`requires_cross_file_context == True`). Isolating retrieval from
generation matters: if the retriever never surfaces the right context, no
conclusion about the LLM's use of context is interpretable.

Stdlib only -- runs without project dependencies installed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from reviewmind.parsing.chunker import chunk_repo  # noqa: E402
from reviewmind.retrieval.bm25 import BM25Retriever  # noqa: E402
from reviewmind.retrieval.callgraph import HybridRetriever  # noqa: E402
from reviewmind.retrieval.graph import GraphRetriever  # noqa: E402

DATASET = ROOT / "experiments" / "dataset" / "cases"
CORPUS = ROOT / "experiments" / "corpus" / "taskapi"

# The chunk that makes each cross-file case recognisable as a defect.
# Fixed from the defect's construction, not from any result.
CRITICAL_CHUNK = {
    "null_deref_001": "fetch_user",
    "null_deref_002": "fetch_task",
    "broken_contract_001": "handle_update_email",
    "broken_contract_002": "handle_user_summary",
}

K_VALUES = (1, 2, 3, 4, 6, 8)


def load_cases() -> dict:
    cases = {}
    for cid, need in CRITICAL_CHUNK.items():
        rec = json.loads((DATASET / cid / "case.json").read_text())
        tf = rec["target_file"]
        src = (DATASET / cid / "after" / tf).read_text()
        cases[cid] = {"rec": rec, "target_file": tf, "source": src, "need": need}
    return cases


def rank_of(hits, need: str) -> int | None:
    for i, h in enumerate(hits, start=1):
        if h.chunk.name.split(".")[-1] == need:
            return i
    return None


def main() -> int:
    chunks = chunk_repo(CORPUS)
    retrievers = {
        "bm25": BM25Retriever(chunks),
        "+callee": HybridRetriever(chunks),
        "+bidir": GraphRetriever(chunks),
    }
    cases = load_cases()

    def search(key, r, c, k):
        kw = {"top_k": k, "exclude_files": [c["target_file"]]}
        if key == "+callee":
            kw["changed_file_source"] = c["source"]
        elif key == "+bidir":
            kw["changed_file_source"] = c["source"]
            kw["changed_file"] = c["target_file"]
        return r.search(c["rec"]["diff"], **kw)

    # ---- context-recall@k ------------------------------------------------- #
    print(f"Index: {len(chunks)} chunks from {CORPUS.name}")
    print(f"Cases: {len(cases)} cross-file defects\n")
    print("Context-recall@k -- did the retriever surface the critical chunk?\n")
    header = f"{'k':>3s}" + "".join(f"{name:>10s}" for name in retrievers)
    print(header)
    print("-" * len(header))

    table = {}
    for k in K_VALUES:
        row = []
        for key, r in retrievers.items():
            n = sum(1 for c in cases.values() if rank_of(search(key, r, c, k), c["need"]))
            row.append(n)
            table[(key, k)] = n
        print(f"{k:>3d}" + "".join(f"{n}/{len(cases)}".rjust(10) for n in row))

    # ---- per-case ranks at a fixed budget --------------------------------- #
    print("\nRank of the critical chunk at k=6 (lower is better):\n")
    header2 = f"{'case':24s}{'needed':22s}" + "".join(f"{n:>10s}" for n in retrievers)
    print(header2)
    print("-" * len(header2))
    for cid, c in cases.items():
        cells = []
        for key, r in retrievers.items():
            rk = rank_of(search(key, r, c, 6), c["need"])
            cells.append(str(rk) if rk else "MISS")
        print(f"{cid:24s}{c['need']:22s}" + "".join(f"{x:>10s}" for x in cells))

    # ---- machine-readable ------------------------------------------------- #
    out = ROOT / "results" / "retrieval_ablation.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "n_chunks": len(chunks),
        "n_cases": len(cases),
        "critical_chunks": CRITICAL_CHUNK,
        "context_recall_at_k": {
            key: {str(k): table[(key, k)] for k in K_VALUES} for key in retrievers
        },
        "ranks_at_k6": {
            cid: {
                key: rank_of(search(key, r, c, 6), c["need"])
                for key, r in retrievers.items()
            }
            for cid, c in cases.items()
        },
    }, indent=2))
    print(f"\nWrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
