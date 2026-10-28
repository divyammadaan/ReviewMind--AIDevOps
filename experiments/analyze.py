"""Score a run and print the results tables.

Usage:
    .venv/bin/python experiments/analyze.py                      # newest run
    .venv/bin/python experiments/analyze.py results/run_<stamp>   # a specific run

REFUSES to print a results table when the run's config says
`valid_for_reporting: false` (i.e. the mock backend was involved), unless
--force-mock is passed. This is a guard against a pipeline test being copied
into a report as though it were an experimental result.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "experiments"))

from scoring import SENSITIVITY_TOLERANCES, score_arm, sensitivity_sweep  # noqa: E402

DATASET = ROOT / "experiments" / "dataset"
RESULTS = ROOT / "results"


def newest_run() -> Path:
    """Newest run that actually has results.

    A run directory is created when the run STARTS, so an interrupted run leaves
    an empty directory behind. Picking it blindly made this tool report on a run
    that had produced nothing.
    """
    runs = sorted(p for p in RESULTS.glob("run_*") if p.is_dir())
    usable = [r for r in runs if (r / "raw" / "results.json").exists()
              and (r / "config.json").exists()]
    if not usable:
        raise SystemExit(
            "No complete runs found in results/.\n"
            f"({len(runs)} run directories exist but none contain results.json + config.json)\n"
            "Run experiments/run_experiment.py first."
        )
    skipped = len(runs) - len(usable)
    if skipped:
        print(f"note: skipped {skipped} incomplete run director"
              f"{'y' if skipped == 1 else 'ies'} (interrupted before writing)\n")
    return usable[-1]


def load_labels() -> dict[str, dict]:
    out = {}
    for entry in json.loads((DATASET / "manifest.json").read_text())["cases"]:
        cid = entry["case_id"]
        out[cid] = json.loads((DATASET / "cases" / cid / "case.json").read_text())
    return out


def _bar(frac: float, width: int = 18) -> str:
    filled = round(frac * width)
    return "#" * filled + "." * (width - filled)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run", nargs="?", default=None)
    ap.add_argument("--force-mock", action="store_true",
                    help="print tables even for a mock run (never use for a report)")
    args = ap.parse_args()

    run_dir = Path(args.run) if args.run else newest_run()
    config = json.loads((run_dir / "config.json").read_text())
    records = json.loads((run_dir / "raw" / "results.json").read_text())
    labels = load_labels()

    print(f"run:        {run_dir.name}")
    print(f"model:      {config.get('model')}")
    print(f"mode:       {config.get('mode')}   repeats: {config.get('repeats')}")
    print(f"retriever:  {config.get('retriever')}   top_k: {config.get('top_k')}")
    print(f"commit:     {config.get('git_commit')}")
    print(f"platform:   {config.get('platform')}  python {config.get('python')}")
    print()

    if not config.get("valid_for_reporting", False):
        print("!" * 72)
        print("!! valid_for_reporting = FALSE")
        print(f"!! {config.get('validity_note','')}")
        print("!" * 72)
        if not args.force_mock:
            print("\nRefusing to print a results table. Pass --force-mock to override\n"
                  "(for debugging only -- these numbers must never appear in a report).")
            return 2
        print()

    arms = [a for a in ("static", "llm_diff", "llm_rag")
            if any(r["arm"] == a for r in records)]

    scores = {}
    for arm in arms:
        rows = [r for r in records if r["arm"] == arm]
        scores[arm] = score_arm(arm, rows, labels)

    # ---- headline ---------------------------------------------------------- #
    print("=" * 72)
    print("PRIMARY RESULTS")
    print("=" * 72)
    hdr = (f"{'arm':10s} {'pos.rec':>8s} {'sem.rec':>8s} {'sem 95% CI':>14s} "
           f"{'prec(lb)':>9s} {'iss/case':>9s} {'med s':>7s}")
    print(hdr); print("-" * len(hdr))
    for arm in arms:
        sc = scores[arm]
        lo, hi = sc.recall_semantic_ci
        print(f"{arm:10s} {sc.recall:7.1%} {sc.recall_semantic:7.1%} "
              f"{f'[{lo:.2f},{hi:.2f}]':>14s} "
              f"{sc.precision_lower_bound:8.1%} "
              f"{sc.issues_per_case:9.1f} "
              f"{sc.median_latency_s:7.2f}")
    print()
    print("pos.rec = positional match only (file + line within tolerance).")
    print("sem.rec = positional AND the report describes the ACTUAL defect.")
    print("          The gap between them is how often an arm flagged the right")
    print("          LOCATION while diagnosing the WRONG PROBLEM.")
    print("prec(lb) = precision LOWER BOUND (see RESEARCH_DESIGN 6.2).")
    print()

    # ---- H3: cross-file vs local ------------------------------------------ #
    print("=" * 72)
    print("RECALL BY CONTEXT NEED   (tests H3: retrieval should help SELECTIVELY)")
    print("=" * 72)
    hdr2 = f"{'arm':10s} {'cross-file':>22s} {'local':>22s}"
    print(hdr2); print("-" * len(hdr2))
    for arm in arms:
        d = scores[arm].recall_by_context_need()
        cf, lc = d["cross_file"], d["local"]
        cf_s = f"{cf[0]}/{cf[1]} ({cf[0]/cf[1]:.0%})" if cf[1] else "n/a"
        lc_s = f"{lc[0]}/{lc[1]} ({lc[0]/lc[1]:.0%})" if lc[1] else "n/a"
        print(f"{arm:10s} {cf_s:>22s} {lc_s:>22s}")
    print()

    # ---- RQ2: the decisive breakdown --------------------------------------- #
    print("=" * 72)
    print("RECALL BY INJECTION MODE   <-- THE VALID TEST OF RQ2")
    print("=" * 72)
    print("deletion-mode defects LEAK their ground truth: the removed lines state")
    print("the contract being violated, so the diff-only arm can read the answer")
    print("off the diff. Only the addition-mode column tests retrieval.")
    print()
    hdrm = (f"{'arm':10s} {'deletion POS':>14s} {'deletion SEM':>14s}"
            f" {'addition POS':>14s} {'addition SEM':>14s}")
    print(hdrm); print("-" * len(hdrm))
    for arm in arms:
        pos = scores[arm].recall_by_injection_mode()
        sem = scores[arm].semantic_by_injection_mode()
        def cell(d, key):
            v = d.get(key)
            return f"{v[0]}/{v[1]} ({v[0]/v[1]:.0%})" if v and v[1] else "n/a"
        print(f"{arm:10s} {cell(pos,'deletion'):>14s} {cell(sem,'deletion'):>14s}"
              f" {cell(pos,'addition'):>14s} {cell(sem,'addition'):>14s}")
    print()
    print("THE DECISIVE CELL is 'addition SEM': no leaked ground truth, and the")
    print("report must name the real defect. That is the only cell in this table")
    print("that tests whether retrieval supplies genuinely missing context.")
    print()
    hdrc = f"{'arm':10s} {'cross-file+additive POS':>25s} {'... SEM':>12s}"
    print(hdrc); print("-" * len(hdrc))
    for arm in arms:
        dp, tp = scores[arm].recall_cross_file_additive()
        ds, ts = scores[arm].semantic_cross_file_additive()
        cp = f"{dp}/{tp} ({dp/tp:.0%})" if tp else "n/a"
        cs = f"{ds}/{ts} ({ds/ts:.0%})" if ts else "n/a"
        print(f"{arm:10s} {cp:>25s} {cs:>12s}")
    print()

    # ---- per class --------------------------------------------------------- #
    print("=" * 72)
    print("RECALL BY DEFECT CLASS   (tests H1/H2)")
    print("=" * 72)
    all_classes = sorted({c.defect_class for s in scores.values() for c in s.cases})
    hdr3 = f"{'defect class':28s}" + "".join(f"{a:>12s}" for a in arms)
    print(hdr3); print("-" * len(hdr3))
    for cls in all_classes:
        row = f"{cls:28s}"
        for arm in arms:
            d = scores[arm].recall_by_class().get(cls)
            row += f"{f'{d[0]}/{d[1]}' if d else '-':>12s}"
        print(row)
    print()

    # ---- sensitivity ------------------------------------------------------- #
    print("=" * 72)
    print("SENSITIVITY TO THE MATCHING WINDOW")
    print("=" * 72)
    hdr4 = f"{'arm':10s}" + "".join(f"{f'+/-{t}':>10s}" for t in SENSITIVITY_TOLERANCES)
    print(hdr4); print("-" * len(hdr4))
    for arm in arms:
        sweep = sensitivity_sweep(arm, [r for r in records if r["arm"] == arm], labels)
        print(f"{arm:10s}" + "".join(f"{sweep[t]:9.0%} " for t in SENSITIVITY_TOLERANCES))
    print()
    print("A conclusion that holds across all three windows does not depend on")
    print("the tolerance we chose.")
    print()

    # ---- cost -------------------------------------------------------------- #
    print("=" * 72)
    print("COST  (RQ3)")
    print("=" * 72)
    hdr5 = f"{'arm':10s} {'med s':>8s} {'p90 s':>8s} {'tokens':>10s} {'tok/case':>9s}"
    print(hdr5); print("-" * len(hdr5))
    for arm in arms:
        s = scores[arm]
        per = s.total_tokens / s.n_cases if s.n_cases else 0
        print(f"{arm:10s} {s.median_latency_s:8.2f} {s.p90_latency_s:8.2f} "
              f"{s.total_tokens:10d} {per:9.0f}")
    print()

    # ---- errors ------------------------------------------------------------ #
    errs = [(r["arm"], r["case_id"], e) for r in records for e in r.get("errors", [])]
    if errs:
        print("=" * 72)
        print(f"NON-FATAL ERRORS ({len(errs)}) -- reported, not hidden")
        print("=" * 72)
        for arm, cid, e in errs[:15]:
            print(f"  {arm:9s} {cid:28s} {e[:60]}")
        print()

    # ---- machine-readable -------------------------------------------------- #
    summary = {
        "run": run_dir.name,
        "config": config,
        "arms": {a: scores[a].to_dict() for a in arms},
        "n_errors": len(errs),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"wrote {(run_dir / 'summary.json').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
