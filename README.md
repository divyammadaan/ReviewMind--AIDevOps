# ReviewMind — Semantic AI Code Reviewer for GitHub

An AI pull-request reviewer that retrieves related code from across the
repository before judging a change — **and a controlled experiment measuring
whether that retrieval actually helps.**

CSE 4011 — Intelligent Developer Tools and AI DevOps Workflows · A2 Mid-Term

---

## The headline result

14 seeded defects, three arms, identical data and scorer (`openai/gpt-oss-120b`):

| Arm | Sees | Positional recall | **Semantic recall** | Tokens/case |
|---|---|---|---|---|
| `static` (ruff + bandit) | changed files | 28.6% | 21.4% | 0 |
| `llm_diff` | diff only | 92.9% | 78.6% | 1,500 |
| `llm_rag` | diff + retrieved context | **100%** | **100%** | 2,219 |

*Semantic recall* requires the report to describe the **actual** defect, not just
cite a nearby line. The gap between the two columns is how often an arm was
right about *where* and wrong about *what*.

On the only non-leaky test bed for the retrieval question:
**static 0/4 · diff-only 2/4 · RAG 4/4.**

> **Read `docs/LIMITATIONS.md` before quoting any of this.** n=14 overall and
> n=4 in the decisive cell. No statistical significance is claimed.

## Retrieval ablation (no LLM involved)

| k | BM25 | +callee | **+bidirectional** |
|---|---|---|---|
| 1 | 1/4 | 1/4 | **4/4** |
| 3 | 3/4 | 3/4 | **4/4** |
| 6 | 4/4 | 4/4 | 4/4 |

Bidirectional call-graph retrieval reaches at k=1 what BM25 needs k=6 for.
BM25 *does* get there — the gain is **rank**, not reachability.

## Quick start

```bash
sudo apt install python3.14-venv          # Ubuntu ships 3.14 without ensurepip
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env                       # add your GROQ_API_KEY
```

Runs with **no API key** (stdlib only):
```bash
.venv/bin/python experiments/seed_bugs.py
.venv/bin/python experiments/retrieval_ablation.py
.venv/bin/python experiments/run_experiment.py --arms static
.venv/bin/python experiments/analyze.py
```

Full experiment (needs `GROQ_API_KEY`):
```bash
.venv/bin/python experiments/run_experiment.py \
  --arms static,llm_diff,llm_rag --mode live
.venv/bin/python experiments/analyze.py
```

## Layout

```
reviewmind/
  parsing/chunker.py        stdlib `ast` chunking -> functions/classes
  retrieval/bm25.py         pure-Python BM25 (always available)
  retrieval/callgraph.py    callee expansion (kept: it FAILED, and the ablation needs it)
  retrieval/graph.py        bidirectional call-graph retrieval (default)
  review/prompts.py         one prompt builder shared by both LLM arms, by design
  review/llm.py             Groq client + mock backend + retry
  review/throttle.py        token-per-minute budget
  arms/static_arm.py        Arm A — ruff + bandit
  arms/llm_arm.py           Arms B and C (one function; retriever optional)
  qa/bot.py                 code Q&A, no LangChain dependency
  qa/langchain_bot.py       same, orchestrated by LangChain (BaseRetriever + LCEL)
  navigation/sourcegraph.py Sourcegraph semantic navigation
  automation/issue_to_pr.py issue -> PR (replaces defunct Sweep.dev)
  github/                   diff fetch + inline PR comments
experiments/
  bugs/catalog.py           14 seeded defects
  bugs/concepts.py          semantic scoring rubrics
  seed_bugs.py              dataset generator (stdlib only)
  run_experiment.py         runner
  scoring.py                metrics, Wilson CIs, sensitivity sweep
  analyze.py                results tables
```

## Safety properties

- **Never executes pull-request code.** Diffs are parsed with `ast`; only text
  reaches the LLM. The CI workflow never installs the PR's dependencies.
- Uses `pull_request`, **not** `pull_request_target` — no privilege-escalation path.
- The issue→PR automation has a **default-deny path allow-list** (10/10 gate
  tests): it cannot modify `.github/`, workflows, `requirements`, `Dockerfile`
  or `.env`, so it cannot rewrite its own pipeline. It never merges.
- `.env` is gitignored and `chmod 600`.

## Documentation

| Document | Contents |
|---|---|
| `docs/RESEARCH_DESIGN.md` | RQ, hypotheses, design, metrics, deviations |
| `docs/LIMITATIONS.md` | **Three validity threats found by measurement** |
| `docs/TOOL_COVERAGE.md` | Every required tool, with evidence of status |
| `docs/PROGRESS_REPORT.md` | A2 progress report |
| `docs/CHARTER.md` / `TIMELINE.md` | Charter and progress-vs-plan |
| `docs/VIVA_PREP.md` | Defence notes |
