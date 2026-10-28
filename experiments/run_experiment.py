"""Run the ReviewMind experiment across arms and cases.

Usage
-----
    # Arm A only -- no API key needed
    .venv/bin/python experiments/run_experiment.py --arms static

    # all arms, live LLM (requires GROQ_API_KEY in .env)
    .venv/bin/python experiments/run_experiment.py --arms static,llm_diff,llm_rag --mode live

    # repeat runs, to measure variance
    .venv/bin/python experiments/run_experiment.py --arms llm_rag --mode live --repeats 3

Every run writes results/run_<timestamp>/ containing the raw per-case output, the
run configuration, and a `valid_for_reporting` flag. The flag is false whenever
any response came from the mock backend, so a wiring test can never be mistaken
for an experimental result.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from reviewmind.arms import llm_arm, static_arm  # noqa: E402
from reviewmind.parsing.chunker import chunk_repo  # noqa: E402
from reviewmind.retrieval.bm25 import BM25Retriever  # noqa: E402
from reviewmind.retrieval.callgraph import HybridRetriever  # noqa: E402
from reviewmind.retrieval.graph import GraphRetriever  # noqa: E402
from reviewmind.review.llm import build_llm  # noqa: E402

DATASET = ROOT / "experiments" / "dataset"
RESULTS = ROOT / "results"

ALL_ARMS = ("static", "llm_diff", "llm_rag")
RETRIEVERS = {"bm25": BM25Retriever, "callee": HybridRetriever, "bidir": GraphRetriever}


def load_cases() -> list[dict]:
    manifest = DATASET / "manifest.json"
    if not manifest.exists():
        raise SystemExit(
            f"No dataset found at {manifest}.\n"
            "Generate it first:  python3 experiments/seed_bugs.py"
        )
    out = []
    for entry in json.loads(manifest.read_text())["cases"]:
        out.append(json.loads((DATASET / "cases" / entry["case_id"] / "case.json").read_text()))
    return out


def git_commit() -> str:
    """Record the code version, so a result can be tied to the code that produced it."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, cwd=str(ROOT), timeout=5,
        ).stdout.strip() or "not-a-git-repo"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def run(arms: list[str], mode: str, repeats: int, top_k: int,
        retriever_key: str, limit: int | None,
        injection_mode: str | None = None) -> Path:
    cases = load_cases()
    if injection_mode:
        cases = [c for c in cases if c.get("injection_mode") == injection_mode]
    if limit:
        cases = cases[:limit]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = RESULTS / f"run_{stamp}"
    (out_dir / "raw").mkdir(parents=True, exist_ok=True)

    llm = build_llm(mode=mode) if any(a.startswith("llm") for a in arms) else None
    used_mock = False
    records: list[dict] = []

    # Write after EVERY case rather than only at the end. Two 3-repeat runs were
    # killed by rate-limit backoff and lost all completed work, because results
    # were only serialised on normal exit. Partial results from an interrupted
    # run are still scientifically usable (they are complete per case); losing
    # them is pure waste.
    raw_path = out_dir / "raw" / "results.json"

    def flush() -> None:
        raw_path.write_text(json.dumps(records, indent=2))

    print(f"cases={len(cases)}  arms={arms}  repeats={repeats}  mode={mode}")
    if "llm_rag" in arms:
        print(f"retriever={retriever_key}  top_k={top_k}")
    print()

    for rep in range(1, repeats + 1):
        for case in cases:
            cid = case["case_id"]
            case_dir = DATASET / "cases" / cid / "after"
            changed = case["target_file"]

            for arm in arms:
                t0 = time.perf_counter()

                if arm == "static":
                    res = static_arm.review(cid, case_dir, [changed])

                elif arm == "llm_diff":
                    res = llm_arm.review(cid, case["diff"], llm)

                elif arm == "llm_rag":
                    # Index is built per case because each case is a distinct
                    # repository snapshot; sharing one index across cases would
                    # leak the clean version of a defective file into retrieval.
                    retriever = RETRIEVERS[retriever_key](chunk_repo(case_dir))
                    res = llm_arm.review(
                        cid, case["diff"], llm, retriever=retriever,
                        repo_dir=case_dir, changed_file=changed, top_k=top_k,
                    )
                else:
                    raise SystemExit(f"unknown arm: {arm}")

                if res.model == "mock":
                    used_mock = True

                rec = res.to_dict()
                rec["repeat"] = rep
                records.append(rec)
                flush()

                flag = "!" if res.errors else " "
                print(f"  [rep{rep}] {arm:9s} {cid:28s} "
                      f"{len(res.issues):>2d} issues  {time.perf_counter()-t0:5.2f}s {flag}")

    flush()

    config = {
        "timestamp_utc": stamp,
        "arms": arms,
        "mode": mode,
        "repeats": repeats,
        "top_k": top_k,
        "retriever": retriever_key if "llm_rag" in arms else None,
        "n_cases": len(cases),
        "n_records": len(records),
        "complete": len(records) == len(cases) * len(arms) * repeats,
        "injection_mode_filter": injection_mode,
        "model": getattr(llm, "model", None),
        "git_commit": git_commit(),
        "python": platform.python_version(),
        "platform": f"{platform.system()}-{platform.machine()}",
        # The guard against reporting a wiring test as a result.
        "valid_for_reporting": not used_mock,
        "validity_note": (
            "MOCK backend was used for at least one response; these numbers are "
            "pipeline-validation output, NOT experimental results."
            if used_mock else
            "All responses came from a live model."
        ),
    }
    (out_dir / "config.json").write_text(json.dumps(config, indent=2))

    print()
    if used_mock:
        print("*** valid_for_reporting = FALSE (mock backend used) ***")
    print(f"wrote {out_dir.relative_to(ROOT)}")
    return out_dir


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="static",
                    help=f"comma-separated subset of {','.join(ALL_ARMS)}")
    ap.add_argument("--mode", default=None, choices=["mock", "live"],
                    help="LLM backend; defaults to REVIEWMIND_LLM_MODE or mock")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--top-k", type=int, default=6)
    ap.add_argument("--retriever", default="bidir", choices=list(RETRIEVERS))
    ap.add_argument("--limit", type=int, default=None, help="first N cases only")
    ap.add_argument("--injection-mode", default=None, choices=["deletion", "addition"],
                    help="restrict to one injection mode (addition = the valid RQ2 test bed)")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    bad = set(arms) - set(ALL_ARMS)
    if bad:
        raise SystemExit(f"unknown arms: {sorted(bad)}")

    run(arms, args.mode or "mock", args.repeats, args.top_k, args.retriever,
        args.limit, args.injection_mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
