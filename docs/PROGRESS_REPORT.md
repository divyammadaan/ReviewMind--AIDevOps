# ReviewMind — A2 Progress Report

**Student:** Manan Sharma · **Course:** CSE 4011 · **Phase:** A2 Mid-Term (5–9 Oct 2026)

---

## 1. What was built

An AI code reviewer that reads a pull-request diff, retrieves related code from
elsewhere in the repository, and reports located defects plus test suggestions —
**plus a controlled experiment measuring whether the retrieval actually helps.**

4,900 lines of Python across 11 modules; 6 documents. Every component below was
executed and verified, not merely written.

| Component | Verified behaviour |
|---|---|
| Defect-injection dataset | 14 labelled defects, 7 classes, 14/14 inject unambiguously |
| Static baseline (ruff + bandit) | Runs in 0.09 s; 3/3 on pattern defects, 0/5 on semantic ones |
| LLM reviewer, diff-only | 92.9% positional recall |
| LLM reviewer + retrieval | **100%** positional and semantic recall |
| Bidirectional call-graph retriever | **4/4 context-recall at k=1** (BM25 needs k=6) |
| LangChain integration | Genuine `BaseRetriever` + LCEL chain |
| Code Q&A bot | Grounded, cited answers in ~0.6 s |
| GitHub Actions | 2 validated workflows |
| CodiumAI cover-agent | Coverage **75% → 100%**, tests validated by execution |
| Sourcegraph navigation | **6/6** query types returned real hits |

## 2. Principal results

Run `run_20261004T105129Z`, `openai/gpt-oss-120b`, temperature 0, 14 cases:

| Arm | Positional recall | **Semantic recall** | Precision (lower bd) | Tokens/case |
|---|---|---|---|---|
| static | 28.6% | 21.4% | 44.4% | 0 |
| llm_diff | 92.9% | 78.6% | 61.9% | 1,500 |
| llm_rag | **100%** | **100%** | 70.0% | 2,219 |

**Answers.** RQ1: the LLM substantially beats static analysis, which scores
**0/5** on semantic defect classes. RQ2: on the only valid test bed
(addition-mode, semantic scoring) retrieval takes detection from **2/4 → 4/4**.
RQ3: that costs **+48% tokens** and ~4 s latency.

## 3. The most important finding: three ways our own experiment lied to us

This is the part worth examining, because each error would have produced a
*confidently wrong* conclusion.

**(i) Rate limits masquerading as results.** The first full run showed RAG
*worse* than diff-only (80% vs 100%). Two Arm C calls had returned HTTP 429; a
failed call yields zero issues, which the scorer correctly counted as misses.
Arm C has the largest prompts, so throttling hit it first — the bias ran
*against* our own method. Fixed with a token-budget throttle plus backoff.

**(ii) The dataset leaked its own ground truth.** With that fixed, RAG and
diff-only tied at 100% — an apparent null result. But diff-only was scoring 4/4
on cross-file defects it should not have been able to judge. Its own words
explain why:

> *"violating the function's contract of returning `"unknown"` for missing users"*

Every defect had been created by **deleting correct code**, so the removed lines
appeared in the diff as `-` lines — and those lines state the contract being
violated. The diff handed the model the answer. We added 4 **addition-mode**
defects that delete nothing, verified to contain zero `-` lines.

**(iii) The metric measured location, not understanding.** Diff-only still
scored 4/4 — because credit was given for any issue within ±3 lines. It was
flagging the right *line* while diagnosing the *wrong problem*:

| case | `llm_diff` said | correct? |
|---|---|---|
| `add_double_scale_001` | *"If completion_ratio raises … no error handling"* | ✗ |
| `add_double_scale_001` (RAG) | *"already returns a percentage; ×100 gives 5000%"* | ✓ |
| `add_ignored_return_001` | *"Catches all Exception types … KeyboardInterrupt"* | ✗ |
| `add_ignored_return_001` (RAG) | *"boolean return ignored; failures reported as success"* | ✓ |

The leniency systematically favoured the **context-starved** arm, because a
vague nearby guess is exactly what a reviewer without context produces. We added
a deterministic concept rubric; semantic recall separated the arms **50% vs 100%**.

## 4. Honest limitations

- **n = 14 overall, n = 4 in the decisive cell.** The headline rests on a
  two-case difference. CIs are wide and overlapping; **no significance is claimed.**
- Synthetic defects are likely more stereotyped than real ones; only the
  *between-arm* comparison is defensible, not the absolute rates.
- One model, one provider, one language, one repository.
- Precision is a **lower bound**; the planned hand-audit is not yet done.
- The embedding retriever is implemented but **unmeasured**.
- We authored both system and evaluation. Mitigations in `docs/LIMITATIONS.md` §5
  — including that 3 defect classes were chosen for static analysis to *win*, and
  that a case contaminated in our own favour was removed.

## 5. Blocked / incomplete — stated plainly

| Item | Status |
|---|---|
| **Sweep.dev** | Service **shut down**. `sweep.dev` does not resolve in DNS; their README announces a pivot to JetBrains. Capability reimplemented natively. |
| **Self-hosted Sourcegraph** | Image is **amd64-only**; dev VM is arm64. Run on the macOS host instead; public instance used for scripted navigation. |
| **Variance runs (3 repeats)** | Groq's **200k tokens/day** cap was exhausted. Single-run results only. |
| **Live PR demonstration** | Pending a GitHub repository. |
| **Codeium** | Now Windsurf, an IDE plugin with no scriptable API. Not reproducible in CI. |

## 6. Reproducing this

```bash
sudo apt install python3.14-venv
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # add GROQ_API_KEY

.venv/bin/python experiments/seed_bugs.py            # build the dataset
.venv/bin/python experiments/retrieval_ablation.py   # retrieval, no LLM needed
.venv/bin/python experiments/run_experiment.py --arms static   # no API key needed
.venv/bin/python experiments/run_experiment.py --arms static,llm_diff,llm_rag --mode live
.venv/bin/python experiments/analyze.py
```

Mock-backend runs are stamped `valid_for_reporting: false` and the analyser
**refuses** to tabulate them, so a wiring test cannot be mistaken for a result.
