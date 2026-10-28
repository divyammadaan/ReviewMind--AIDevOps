# ReviewMind — Limitations, Failure Cases and Threats to Validity

Faculty criterion: *"Understanding of what works and what does not, discussion of
limitations, failure cases, assumptions, and possible sources of error or bias."*

This document is deliberately unflattering. Every item below was found by
measurement during development, not anticipated in advance, and each is recorded
with the evidence that exposed it.

---

## 1. Failures we found in our own method

### 1.1 Lexical retrieval cannot reach callees (FIXED, measured)

**Symptom.** The first BM25 retriever ranked the breaking *caller* first on
`broken_contract_001` (score 48.96) but placed the critical *callee*
`db.fetch_user` only 5th on `null_deref_001`.

**Cause.** A diff that deletes a `None` check shares identifier vocabulary with
functions that *call* the changed code, but need not mention the code it
*calls*. `get_user → db.fetch_user` is one hop beyond the diff's vocabulary, so
a purely lexical scorer cannot reach it — yet `fetch_user`'s `Optional` return is
exactly what makes the change a defect.

**Fix and its own failure.** Adding callee-only expansion fixed that case but
*regressed* caller cases: `broken_contract_002` fell from rank 3 to rank 6
because structural hits consumed the retrieval budget. Reserving separate budget
per direction (callees *and* callers) resolved both.

**Measured outcome — context-recall@k on the 4 cross-file cases:**

| k | bm25 | +callee | +bidirectional |
|---|------|---------|----------------|
| 1 | 1/4 | 1/4 | **4/4** |
| 2 | 2/4 | 2/4 | **4/4** |
| 3 | 3/4 | 3/4 | **4/4** |
| 6 | 4/4 | 4/4 | 4/4 |

Honest reading: **BM25 also reaches 4/4 by k=6.** The bidirectional retriever's
advantage is *rank*, not raw presence — it achieves at k=1 what BM25 needs k=6
for. That matters for token cost, not for whether retrieval is possible at all.
Claiming it "finds context BM25 cannot find" would overstate the result.

### 1.2 Retrieval is lexical + structural, not semantic

The guaranteed retrieval path is BM25 plus `ast` call-graph resolution. Neither
is a semantic embedding model. Two consequences:

- Renamed concepts with no shared tokens and no call edge will not be retrieved.
- Our "semantic code search" is weaker than the proposal's ChromaDB/embedding
  design. The embedding backend is implemented but optional (`requirements-optional.txt`)
  because Python 3.14 wheel availability was unverified. **We have not measured
  the embedding retriever**, so no claim is made about it.

### 1.3 The system never executes code

Deliberate (safety), but it means whole defect classes are out of reach:
concurrency bugs, performance regressions, and anything requiring runtime state.
ReviewMind reasons about code it has only read.

---

## 2. Failures we found in our own *experiment* (threats to validity)

These are more serious than §1, because they would have produced wrong
conclusions rather than weak ones.

### 2.1 The dataset leaked its own ground truth (CRITICAL)

**Symptom.** Arm B (diff-only) scored **4/4 on cross-file defects it should not
have been able to judge**, giving a null result for RQ2.

**Cause.** Every original defect was created by *deleting correct code*, so the
removed lines appear in the diff as `-` lines — and those lines state the very
contract being violated. Evidence, from `llm_diff` on `null_deref_001`:

> *"violating the function's contract of returning `"unknown"` for missing users"*

The model did not need `db.py`; it read the deleted guard. On
`broken_contract_002` it said *"as implied by the **previous implementation**"* —
and hedged *"likely by its docstring or callers"*, i.e. it was **guessing** at
context rather than knowing it.

**Why this is a design error, not bad luck.** Our `requires_cross_file_context`
flag was assigned from each defect's *nature*, but the *injection method*
determined what the diff revealed. The two came apart and we did not notice
until we read the model's reasoning.

**Fix.** Four **addition-mode** defects that add new wrong code and delete
nothing, verified programmatically to contain zero `-` lines. Only these can
test RQ2.

**Residual limitation.** 4 addition-mode cases is a very small test bed. The
decisive comparison rests on a difference of 2 cases.

### 2.2 Positional matching over-credited the weaker arm (CRITICAL)

**Symptom.** Even on addition-mode cases, Arm B scored 4/4 — apparently
confirming the null result.

**Cause.** Our matching rule credited any issue within ±3 lines of the defect
span. Arm B earned credit for flagging the right *location* while diagnosing a
*different problem*:

| case | `llm_diff` said | correct? |
|---|---|---|
| `add_double_scale_001` | *"If completion_ratio raises … no error handling"* | **No** — wrong defect |
| `add_ignored_return_001` | *"Catches all Exception types … KeyboardInterrupt"* | **No** — generic lint |
| `add_double_scale_001` (`llm_rag`) | *"completion_ratio already returns a percentage; multiplying by 100 produces 5000%"* | **Yes** |
| `add_ignored_return_001` (`llm_rag`) | *"update_email's boolean return is ignored; failures reported as success"* | **Yes** |

Recall was measuring **location, not understanding** — and the leniency
systematically favoured the context-starved arm, because a vague nearby guess is
precisely what a reviewer without context produces.

**Fix.** A per-defect concept rubric (`experiments/bugs/concepts.py`); a
*semantic* detection requires a positional match **and** mention of the concepts
a correct diagnosis must contain. Both figures are reported, and the gap between
them is itself informative.

**Limitation of the fix.** Keyword rubrics yield false negatives on unanticipated
phrasing, so semantic recall is a **lower bound**. Rubrics were authored from
each defect's `why_wrong` field (written before any run), but they were *added*
after observing outputs — a real bias risk, mitigated only by their being
deterministic, auditable, and applied identically to all arms. An LLM judge was
rejected to avoid using a model to grade a model.

### 2.3 Rate limits masqueraded as results (CRITICAL)

In the first full three-arm run, two Arm C calls returned HTTP 429. A failed
call yields zero issues, which the scorer correctly counted as misses — making
**RAG appear worse than diff-only (80% vs 100%)**. That difference was caused
entirely by API throttling.

Arm C has the largest prompts, so throttling hits it first and hardest: the bias
ran *against* our own method. Fixed with exponential backoff honouring
`Retry-After`. Errors surviving all retries are still recorded and still counted
as misses — we do not drop inconvenient cases.

**Standing risk.** Any hosted-API experiment can silently convert
infrastructure failure into apparent result. Always read the error column before
the results table.

### 2.4 Precision is a lower bound

An arm may flag a genuine pre-existing problem we did not seed; our matcher
counts it as a false positive. This understates broad reviewers (the LLM arms)
and flatters narrow ones. The planned hand-audit of a 20-comment sample to
estimate the correction factor **has not yet been performed**, so all reported
precision figures remain lower bounds.

---

## 3. Assumptions

1. Seeded defects resemble real ones. Unverified; injected bugs are likely more
   stereotyped and easier.
2. The concept rubric captures what "finding the bug" means.
3. Groq's hosted model is stable across runs. Partly tested via repeats.
4. `ast` chunking at function granularity is the right retrieval unit. Untested
   against alternatives.
5. Line numbers from an LLM are comparable to line numbers from a linter.

---

## 4. What we cannot conclude

- **Nothing about "LLMs" in general.** One model (`openai/gpt-oss-120b`), one
  provider.
- **Nothing about statistical significance.** With n=14 overall and n=4 in the
  decisive cell, confidence intervals are wide and overlapping. We report CIs and
  claim no significance.
- **Nothing about generality** across languages, repository sizes, or real PRs.
- **Nothing about the embedding retriever**, which was never measured.
- **Nothing about absolute detection rates** in production. Only the
  *between-arm* comparison on this dataset is defensible.

---

## 5. Conflict of interest

We authored both the system and its evaluation. Mitigations, all verifiable in
the repository:

- Defect classes were fixed before any arm ran, and **include three classes
  chosen for static analysis to win** (`hardcoded_secret`, `bare_except`,
  `sql_injection`) — static analysis duly scores 3/3 on them.
- The static baseline runs ruff's bug-oriented rule families (`E,F,B,S,SIM,RET,ARG,TRY`)
  and bandit at low thresholds, not a weakened default.
- A case contaminated in our own favour was **removed**: `add_null_deref_002`
  originally sat in `api.py` where `get_task` was unimported, letting ruff detect
  it as `F821` for the wrong reason. It was relocated to `task.py`. Keeping it
  would have made the LLM arms look better by comparison.
- Mock-backend runs are stamped `valid_for_reporting: false` and the analyser
  refuses to tabulate them.

### The deeper residual bias

Both §2.1 and §2.2 were discovered because they made our *own method look bad*,
which gave us a motive to investigate. We cannot rule out symmetric errors that
happen to flatter ReviewMind and therefore went uninvestigated. This is the
honest limit of self-evaluation.
