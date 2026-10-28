# ReviewMind — Project Timeline (Progress vs Plan)

Required A2 submission: *"Revised Project Timeline (progress vs plan)"*.
Semester: 27 Jul – 24 Nov 2026.

---

## Phase status

| Phase | Window | Weight | Status |
|---|---|---|---|
| Synopsis / Problem definition | wk of 24 Aug | 5% | ✅ submitted |
| **A1 — Code understanding & navigation** | wk of 21 Sep | 30% | ✅ submitted; Sourcegraph strengthened in Oct |
| **A2 — Tool integration & automation** | **5–9 Oct** | **30%** | 🔄 **in progress** |
| A3 — End-term final project | per calendar | 40% | ⏳ not started |

---

## A2 progress vs plan

| # | Planned task | Status | Evidence |
|---|---|---|---|
| 1 | RAG retrieval over the repo | ✅ done | `reviewmind/retrieval/` — 3 retrievers |
| 2 | LangChain orchestration | ✅ done | `langchain_bot.py` — real `BaseRetriever` + LCEL |
| 3 | Code Q&A bot | ✅ done | grounded answers w/ citations, ~0.6 s |
| 4 | GitHub Actions automation | ✅ done | 2 validated workflows |
| 5 | Sweep.dev automation | ⛔ **blocked — service dead** | capability reimplemented; DNS evidence |
| 6 | CodiumAI test generation | ✅ done | 75% → 100% coverage, verified |
| 7 | Quantitative evaluation | ✅ done | 14 cases, 3 arms, semantic + positional recall |
| 8 | Failure analysis | ✅ done | `docs/LIMITATIONS.md` — 3 validity threats |
| 9 | Documentation | ✅ done | 6 documents |
| 10 | Git repository | 🔄 **outstanding** | not yet initialised |
| 11 | Live PR demonstration | 🔄 **outstanding** | needs the repo |
| 12 | Variance runs (3 repeats) | ⛔ **blocked** | Groq 200k tokens/day exhausted |

**Completed: 9/12 · Outstanding: 2 · Blocked: 2** (one externally, one by quota)

---

## Deviations from the A1 plan, and why

| Planned | Actual | Reason |
|---|---|---|
| Self-hosted Sourcegraph (Docker) | Public instance + Mac host | Image is **amd64-only**; dev VM is arm64 |
| Groq Llama 3.3 | `openai/gpt-oss-120b` | Groq retired Llama 3.3 (`404 model_not_found`) |
| tree-sitter chunking | stdlib `ast` | Python's own parser; no wheel risk on 3.14 |
| ChromaDB vector store | BM25 + call-graph | Guarantees the experiment runs; measured |
| Sweep.dev | Native issue→PR automation | `sweep.dev` does not resolve in DNS |
| Build-only project | Build **+ experiment** | Faculty criteria are research criteria |

---

## Unplanned work that proved necessary

These were not in the plan and consumed significant time, but each fixed a
defect that would otherwise have produced **wrong conclusions**:

1. **Token-budget throttle** — rate limits were silently converting API failures
   into apparent "RAG is worse" results.
2. **Addition-mode defects** — the original dataset leaked its own ground truth
   via deleted lines, making RQ2 untestable.
3. **Semantic scoring rubric** — positional matching credited the diff-only arm
   for flagging the right line while diagnosing the wrong bug.

---

## A3 plan (end-term, 40%)

| Task | Rationale |
|---|---|
| Expand dataset; add real bug-fix PRs | n=14 is the biggest weakness; n=4 in the decisive cell |
| Variance runs + significance testing | Current CIs are wide and overlapping |
| Measure the embedding retriever | Currently implemented but unmeasured |
| Hand-audit precision | Convert the lower bound into a real estimate |
| Second model family | Separate the finding from `gpt-oss-120b` |
| Live PR demonstration | Required for the final demo |
