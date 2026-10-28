# ReviewMind — Research Design (A2 / Mid-Term)

**Course:** CSE 4011 — Intelligent Developer Tools and AI DevOps Workflows
**Phase:** A2 — Project Phase Evaluation 2 (Mid-Term, 5–9 Oct)

> **Scope note.** The original project proposal (`ReviewMind_Project_Proposal.pdf`) is a
> *build* proposal: it specifies an architecture but no research question, baseline, or
> metrics. The faculty mid-term criteria are *research* criteria. This document therefore
> adds an experimental layer on top of the proposal. Everything in §1–§6 is an **addition
> beyond the original proposal**, introduced to satisfy the stated evaluation criteria.
> The architecture in §4 is unchanged from the proposal, minus Sourcegraph (see §8).

---

## 1. Research Question

> **RQ:** When reviewing a code change, does supplying a large language model with
> *repository-wide retrieved context* (RAG) measurably improve its ability to detect
> real defects, compared to (a) giving it only the diff, and (b) conventional static
> analysis?

Decomposed into three testable sub-questions:

| ID | Sub-question | Comparison |
|----|--------------|------------|
| **RQ1** | Does an LLM reviewer detect more seeded defects than conventional static analysis? | Arm A vs Arm B |
| **RQ2** | Does repository-wide retrieval improve LLM defect detection over diff-only review? | Arm B vs Arm C |
| **RQ3** | What does retrieval cost, in latency and tokens, per unit of detection gained? | Arm B vs Arm C |

### Why this question is falsifiable

The question can be answered **no**. Plausible negative outcomes we must be willing to report:

- Retrieval may add irrelevant context that *distracts* the model, lowering precision
  (a documented RAG failure mode).
- Static analysis may beat the LLM on exactly the defect classes it was built for
  (e.g. hardcoded secrets), making the LLM's advantage class-dependent rather than general.
- Retrieval may improve nothing while costing latency — a null result on RQ2 with a
  real cost on RQ3.

We commit in advance to reporting whichever of these occurs. A null result is a valid
answer to this RQ, not a project failure.

---

## 2. Hypotheses

- **H1.** The LLM arms detect more *semantic* defects (missing error handling, incorrect
  logic, off-by-one) than static analysis, because those defects are not pattern-matchable.
- **H2.** Static analysis is competitive or superior on *syntactic / pattern* defects
  (hardcoded secrets, bare `except`), because such rules are precisely what it encodes.
- **H3.** Retrieval helps most for defects whose incorrectness is only visible outside the
  diff — e.g. a caller that assumes a non-`None` return, or a changed function contract.
  For defects fully contained in the diff, retrieval should add little.

H3 is the central claim: **retrieval should help selectively, not uniformly.** This
prediction is more informative than "RAG is better", and it is what we will test per
defect class rather than only in aggregate.

---

## 3. Experimental Design

### 3.1 Three arms

| Arm | Name | Sees | Purpose |
|-----|------|------|---------|
| **A** | `static` | Changed files | Non-AI baseline (`ruff`, `bandit`) |
| **B** | `llm_diff` | Diff only | Isolates the LLM's contribution |
| **C** | `llm_rag` | Diff + retrieved repo context | Full ReviewMind; isolates retrieval |

A→B isolates the value of the **LLM**. B→C isolates the value of **retrieval**.
All three arms run on the identical dataset and are scored by the identical matcher,
so differences are attributable to the arm and not to the data.

### 3.2 Controlled variables

Held constant across arms B and C: model (`llama-3.3-70b-versatile`), temperature
(`0.0`, for determinism), output schema, system persona, and max output tokens.
**Only the context supplied differs.** Arm A has no model.

Temperature is pinned to 0 so that re-runs are reproducible; we additionally report
variance across `N_REPEATS` runs, because temperature 0 does not guarantee bitwise
determinism in hosted inference.

---

## 4. System Under Test

Unchanged from the proposal except as noted in §8.

```
PR event ──► GitHub Actions
                  │
                  ▼
          diff fetch (PyGithub / local git)
                  │
                  ▼
          chunker  ─── Python: stdlib `ast`  (primary)
                   └── other langs: tree-sitter (optional)
                  │
                  ▼
          retriever ── BM25 (pure-Python, always available)
                   └── embeddings + ChromaDB (when installable)
                  │
                  ▼
          reviewer (Groq LLM, structured JSON out)
                  │
                  ▼
          inline PR comments  +  test suggestions
```

**Read-only by design.** ReviewMind parses and analyses code; it never executes code from
a pull request, and never writes to the repository except as PR comments. This is a
deliberate safety property, not an incidental one.

---

## 5. Evaluation Dataset

**Method: controlled defect injection.** We take a small real Python repository, and
programmatically inject known defects, one per generated pull request.

Rationale: ground truth is *exact*. We know the file, the line, and the defect class we
injected, so both **recall** (did the arm find it?) and **precision** (how much of what
it reported was not a seeded defect?) are computable. Mining real bug-fix PRs would be
more authentic but yields noisy labels and could not be completed and verified reliably
before the evaluation. We state this trade-off as a limitation in §7 rather than
overclaiming external validity.

Each dataset item is a record:

```json
{
  "case_id": "null_deref_003",
  "defect_class": "null_dereference",
  "file": "app/services/user.py",
  "line": 42,
  "requires_cross_file_context": true,
  "description": "get_user() may return None; caller dereferences without check",
  "diff": "<unified diff>"
}
```

The `requires_cross_file_context` flag is what allows us to test **H3** directly: we
compare the B→C retrieval gain on cases where it is `true` versus `false`.

### 5.1 Defect classes

| Class | Cross-file? | Detectable by static analysis? |
|-------|-------------|-------------------------------|
| `hardcoded_secret` | No | Yes (`bandit`) |
| `bare_except` | No | Yes (`ruff` E722) |
| `sql_injection` | No | Partially (`bandit`) |
| `off_by_one` | No | No |
| `missing_error_handling` | Sometimes | No |
| `null_dereference` | **Yes** | No |
| `broken_contract` | **Yes** | No |

The mix is deliberate: it spans defects static analysis should win, defects only an LLM
should find, and defects only retrieval should find. A dataset of only one kind would
bias the conclusion.

---

## 6. Metrics

### 6.1 Detection (primary)

- **Recall / detection rate** — fraction of seeded defects correctly flagged.
  Primary metric for RQ1 and RQ2.
- **Precision** — of all issues an arm reported, the fraction corresponding to a
  seeded defect.
- **F1** — harmonic mean, for a single headline comparison.
- **Per-class recall** — the breakdown that tests H1–H3.

### 6.2 A note on precision, stated honestly

Our precision figure is a **lower bound**, and we will label it as such. A reviewer may
flag a genuine pre-existing problem that we did not seed; our matcher counts that as a
false positive even though a human would call it a true positive. Reporting it as
unqualified precision would overstate static analysis and understate the LLM arms.
We therefore additionally hand-audit a 20-comment random sample to estimate how often
an "unmatched" comment is actually legitimate, and report that correction factor.

### 6.3 Cost (for RQ3)

- Wall-clock latency per review (median and p90)
- Prompt + completion tokens per review
- Estimated USD per review

### 6.4 Matching rule

A seeded defect counts as detected when a reported issue (a) names the correct file and
(b) cites a line within **±3 lines** of the injected line. The tolerance absorbs the
off-by-one disagreements inherent in diff line numbering. The window is fixed before
results are examined, so it cannot be tuned to flatter a preferred arm. We report
sensitivity at ±0, ±3 and ±10 to show the conclusion does not depend on this choice.

---

## 7. Known Limitations (expanded in `LIMITATIONS.md`)

1. **Synthetic defects.** Injected bugs may be more stereotyped, and so easier, than
   real-world bugs. Absolute detection rates are therefore optimistic; the
   *between-arm comparison* is the defensible result, not the absolute numbers.
2. **Single repository, single language.** No claim of generality across languages or
   codebase sizes.
3. **Single model family.** Results describe Llama 3.3 via Groq, not "LLMs" in general.
4. **Small N.** With a dataset of tens of cases, modest differences will not be
   statistically significant. We report confidence intervals and refrain from claiming
   significance we have not established.
5. **Lower-bound precision**, as described in §6.2.
6. **We built both the dataset and the system.** This is a conflict of interest in
   defect selection. Mitigation: defect classes were fixed *before* any arm was run,
   and include classes we expect ReviewMind to lose on.

---

## 8. Deviations from the Original Proposal

| Proposal element | Status | Reason |
|------------------|--------|--------|
| Sourcegraph (self-hosted, Docker) | **Deferred to future scope** | Not in the A2 rubric (it is an A1 line item), and the available VM has ~3.3 GB free RAM against Sourcegraph's ~4–8 GB requirement. Deferring avoids an unreliable demo. Repo-wide semantic search is instead provided by our own retrieval layer (§4). |
| tree-sitter chunking | **Optional, with stdlib `ast` primary** | For the Python corpus, the standard library's `ast` module is more reliable and has zero install risk on Python 3.14. tree-sitter is retained behind an interface for other languages. |
| ChromaDB / FAISS | **Optional, with BM25 fallback** | Dependency availability on Python 3.14 is unverified. A pure-Python BM25 retriever guarantees the experiment runs; the embedding retriever is used when installable, and which was used is recorded in every results file. |
| Sweep.dev | **Not integrated** | See `LIMITATIONS.md`. We will not claim an integration we have not built. |
| CodiumAI / Codeium | **Partially addressed** | Test-case suggestion is implemented in ReviewMind's own LLM pipeline rather than via these vendor tools. Stated plainly rather than presented as equivalent. |

Deviations are recorded here so that the report and the viva can address them directly
rather than appear to have overlooked the rubric.
