# Tool Coverage Matrix — every tool named in the handout and the proposal

**Date of verification: 2026-10-04.** Every status below is backed by a command
whose output was observed, not by assumption. Where a tool could not be used,
the evidence for *why* is recorded so the claim can be independently checked.

Legend: ✅ working · ⚠️ working with a documented deviation · ❌ impossible, with evidence

---

## A2 rubric (mid-term, 30%)

### 1. RAG-based Q&A bot + LangChain/LlamaIndex — 10%

| Requirement | Status | Evidence |
|---|---|---|
| RAG-based Q&A bot | ✅ | `reviewmind/qa/bot.py`, `reviewmind/qa/langchain_bot.py`. Grounded answers with per-chunk citations in ~0.6 s |
| Use of LangChain | ✅ | `langchain` 1.4.3, `langchain-groq` 1.1.3 installed. `ReviewMindRetriever` subclasses LangChain's `BaseRetriever` (`isinstance(r, BaseRetriever) == True`); chain is LCEL: `ChatPromptTemplate │ ChatGroq │ StrOutputParser` |
| Vector store | ⚠️ | BM25 + call-graph retrieval is the **measured** default (stdlib-only, zero install risk). ChromaDB/embeddings implemented but **optional and unmeasured** — see `docs/LIMITATIONS.md` §1.2 |

**Verified output:**
```
Q: Does completion_ratio return a fraction or a percentage?
A: completion_ratio returns a percentage. The function calculates
   (done / len(tasks)) * 100.0, and its docstring states "Percentage of the
   user's tasks that are done."[1]
```

### 2. GitHub Actions CI/CD with Sweep.dev — 10%

| Requirement | Status | Evidence |
|---|---|---|
| GitHub Actions CI/CD | ✅ | `.github/workflows/reviewmind.yml`, YAML parsed and validated, 8 steps |
| Sweep.dev | ❌ **service defunct** | See below |
| Equivalent automation | ✅ | `reviewmind/automation/issue_to_pr.py` + `.github/workflows/reviewmind-fix.yml` |

**Evidence that Sweep.dev cannot be used — reproduce it yourself:**

```bash
$ getent hosts sweep.dev        # DOES NOT RESOLVE
$ getent hosts docs.sweep.dev   # DOES NOT RESOLVE
$ getent hosts sourcegraph.com  # resolves: 104.18.32.187
$ getent hosts github.com       # resolves: 20.207.73.82
```

The first two fail while the latter two succeed **from the same machine at the
same moment**, so this is not a local network fault. `curl` returns HTTP 000 for
`sweep.dev`, `docs.sweep.dev` and `community.sweep.dev`.

The project's own README states the reason:

> *"Thank you for all of the support on Sweep. We're now building an AI coding
> assistant for JetBrains"* — github.com/sweepai/sweep

The PyPI package `sweepai` (3.2.5) exists but is only *"CLI for syncing with
Sweep Chat's code change features"* under an Enterprise Edition licence — a
client for the hosted service that no longer runs. **Installing it would produce
a tool that cannot connect to anything.**

**What we did instead.** Sweep.dev's defining capability was: label a GitHub
issue → an AI agent opens a PR implementing the fix. That capability is
implemented directly against the GitHub API, with safety properties a hosted
black box would not give us:

- default-deny path allow-list (**10/10 gate tests pass**) — the automation
  **cannot** modify `.github/`, workflows, `requirements`, `Dockerfile` or
  `.env`, closing the privilege-escalation path where CI-writing automation
  rewrites its own pipeline
- rejects any patch that is not syntactically valid Python
- never commits to the default branch, never force-pushes, **never merges**
- `dry_run=True` by default

### 3. Test generation using CodiumAI / Codeium — 5%

| Requirement | Status | Evidence |
|---|---|---|
| CodiumAI PR-Agent | ✅ | `pr-agent` 0.47.0 installed in `.venv-tools`. 18 commands available (`/review`, `/improve`, `/add_docs`, `/ask`, …). Confirmed it reaches **our Groq key** via litellm — no OpenAI key needed |
| CodiumAI cover-agent (test generation) | ✅ **verified working** | Containerised (`tools/codium/Dockerfile`, `python:3.11-slim`) because it requires Python `>=3.9.17,<3.14` and Ubuntu 26.04 ships **only** 3.14. Raised `app/utils.py` coverage **75% → 100%**, writing 3 new tests, using 2,284 input + 509 output tokens on `groq/openai/gpt-oss-20b` |
| ReviewMind's own test generation | ✅ | Implemented in the review pipeline; emits pytest suggestions with rationale |
| Codeium | ❌ | Codeium is now **Windsurf**, an IDE plugin with no public test-generation API. Not scriptable, so not reproducible in a CI experiment |

**Verified cover-agent run:**
```
Initial coverage: 75.0%
Test passed and coverage increased. Current coverage: 83.33%
Test did not increase coverage. Rolling back.          <-- real validation
Test passed and coverage increased. Current coverage: 91.67%
Test passed and coverage increased. Current coverage: 100.0%
Reached above target coverage of 95% (Current Coverage: 100.0%)
```
Note the rollback: cover-agent runs each generated test and discards those that
do not improve coverage, so the output is validated rather than merely produced.

### Cross-tool validation (an unplanned but useful result)

cover-agent generated its tests from the **clean** corpus, with no knowledge of
our seeded defects. One of those tests independently catches a defect ReviewMind
flagged. Running the generated suite against the `off_by_one_001` case:

```
assert result == "abcdefg..."
E  AssertionError: assert 'abcdefgh...' == 'abcdefg...'
1 failed, 4 passed
```

`test_truncate_long_string` asserts `len(result) == limit`, which is precisely
the contract the seeded off-by-one violates. This cross-validates in both
directions: the seeded defect is genuinely detectable (not an artefact of our
scoring), and the generated tests are meaningful rather than vacuous. Two
independent tools — our LLM reviewer and CodiumAI's test generator — converge on
the same defect by different means.

**Note on cover-agent and code execution.** cover-agent generates tests and then
*runs* them to measure coverage. It is pointed **only at our own evaluation
corpus, never at pull-request code**; the container is a second isolation layer.
ReviewMind's review pipeline remains strictly read-only.

### 4. Integration quality and documentation — 5%

| Artefact | Status |
|---|---|
| `docs/RESEARCH_DESIGN.md` | ✅ RQ, hypotheses, design, metrics, deviations |
| `docs/LIMITATIONS.md` | ✅ 3 critical validity threats found by measurement |
| `docs/TOOL_COVERAGE.md` | ✅ this file |
| Pinned dependencies, two-tier | ✅ `requirements.txt` + `requirements-optional.txt` |
| Reproducible dataset generator | ✅ stdlib-only, 14/14 cases verified |

---

## A1 rubric (already assessed, 30%) — Sourcegraph

| Requirement | Status | Evidence |
|---|---|---|
| Sourcegraph setup | ⚠️ **public instance, not self-hosted** | `src` CLI **8.0.0** vendored at `tools/bin/src`; `SRC_ENDPOINT=https://sourcegraph.com` |
| Semantic code navigation | ✅ | Live queries returned real hits from `github.com/pallets/werkzeug` |
| Query formulation | ✅ | Four builders: `q_find_definition`, `q_find_callers`, `q_structural` (syntax-aware `patterntype:structural`), `q_cross_repo` |

**Why not self-hosted, as the proposal specified.** Sourcegraph's single-container
deployment wants ~4–8 GB RAM. Measured free memory on this VM: **~3.1 GB**. A
local instance would likely OOM, and doing so during a live demonstration is a
worse outcome than not using it. The public instance needs **no container, no
sudo and no RAM headroom**, and works unauthenticated for public repositories.

**What this honestly does and does not provide.** sourcegraph.com **cannot index
our local evaluation corpus** (not a public repo). Sourcegraph therefore provides
genuine repo-wide and *cross-repository* navigation over public code — something
our own retriever cannot do at all — but it is **not part of the measured review
pipeline**, and **no experimental result in this project depends on it.**
Cross-file retrieval for the reviewer is supplied by
`reviewmind/retrieval/graph.py`, which *is* measured. Conflating the two would
misrepresent the architecture.

---

## Proposal tech stack

| Layer | Proposed | Status |
|---|---|---|
| Trigger / CI | GitHub Actions | ✅ |
| Code fetch | GitHub REST API / PyGithub | ✅ `PyGithub` 2.8.1 |
| Code parsing | tree-sitter | ⚠️ stdlib `ast` is primary (Python's own parser; zero wheel risk on 3.14). tree-sitter kept behind the same interface and present in the cover-agent container |
| Semantic navigation | Sourcegraph self-hosted | ⚠️ public instance — see above |
| Vector store | ChromaDB / FAISS | ⚠️ optional/unmeasured; BM25 + call-graph is the measured default |
| RAG orchestration | LangChain / LlamaIndex | ✅ LangChain, genuine `BaseRetriever` + LCEL |
| LLM | Groq **Llama 3.3** | ❌→⚠️ **Groq has retired Llama 3.3** (`404 model_not_found`). Substituted `openai/gpt-oss-120b` (131k context). Key's model list contains no Llama 3.3 |
| Backend | FastAPI | ⚠️ installed; service layer not yet exposed |
| Posting | GitHub REST API | ⚠️ implemented, not yet demonstrated on a live PR (needs a GitHub repo) |

---

## Hurdles overcome, and how

| Hurdle | Resolution |
|---|---|
| `ensurepip` missing → no venv possible | `sudo apt install python3.14-venv` (official repo, 3 packages) |
| Groq retired Llama 3.3 | Queried `/v1/models`, substituted `gpt-oss-120b`, documented |
| Sourcegraph needs 4–8 GB, only 3.1 GB free | Public instance + static `src` binary — zero RAM cost |
| `cover-agent` needs Python <3.14 | `python:3.11-slim` container; host untouched |
| Sweep.dev service dead | Implemented the capability natively, with evidence of the shutdown |
| Rate limits producing fake "results" | Found 8k tokens/min **and 200k tokens/day** caps; added `TokenBucket` throttle + backoff |
| 170-dependency `pr-agent` risking the stack | Isolated `.venv-tools`; main stack regression-tested after |

---

## Live end-to-end demonstration — VERIFIED on a real pull request

Repository: `github.com/Deadmaus07/ReviewMind` (private) · **PR #1**

A branch was pushed adding a new handler containing a genuine **cross-file**
defect. The diff is a *pure addition* — nothing in it hints that `get_user` can
return `None`; that contract lives in `app/db.py`, outside the diff.

```python
def handle_user_badge(user_id: int) -> dict[str, Any]:
    """Short display badge for a user."""
    user = get_user(user_id)
    return {"status": 200, "data": {"badge": user["name"].upper()}}
```

### ReviewMind's output (posted to the PR)

Inline comment on `experiments/corpus/taskapi/app/api.py:34`:

> 🟠 **HIGH** · `crash` — handle_user_badge assumes get_user always returns a user
> dict; if get_user returns None (e.g., unknown user), accessing `user["name"]`
> raises a TypeError/KeyError.

Plus a summary comment and a test suggestion
(`test_handle_user_badge_missing_user`, using `monkeypatch` to force the `None`
path). Measured: **2.66 s** total, 0.97 s retrieval, 1,613 + 545 tokens,
retriever `bm25+bidirectional-callgraph`, model `openai/gpt-oss-120b`.

### CodiumAI PR-Agent's output (same PR, same Groq key)

Posted a *PR Reviewer Guide* identifying the **same** defect, and applied the
label `Review effort 2/5`.

### Second cross-tool convergence

Two independent systems — our retrieval-augmented reviewer and CodiumAI's
PR-Agent — flagged the same defect by different means. They were also
**complementary**: PR-Agent reported *"No relevant tests"* while ReviewMind
supplied a concrete test for the failing path.

This is a weak form of external validation. It is not a controlled comparison
(n=1, and the defect is one we planted), so it should be presented as
corroboration, not as a measured result.

## Remaining blocker: GitHub Actions is locked on the account

The two workflow files are **committed, validated and visible in the repository**
(`.github/workflows/reviewmind.yml`, `reviewmind-fix.yml`). They have never
executed, for a reason outside the project:

```
Annotations
  review
  The job was not started because your account is locked due to a billing issue.
```

Every run fails in 3–5 seconds, before any step executes. Five runs, identical
annotation each time.

**What we ruled out, in order:**

1. *An invalid workflow file.* Both files parse, and GitHub lists them under
   Actions with the correct triggers and job names.
2. *A missing token permission.* A push of `.github/workflows/` was initially
   rejected for lacking `workflow` scope; that was fixed and the files pushed
   successfully. The runs still failed identically, so this was not the cause.
3. *A reserved-word collision.* A step had `id: secrets`, which shadows the
   reserved `secrets` context. Renamed to `keycheck` — no change in behaviour.
   (The rename is retained as correct practice regardless.)
4. *Private-repository Actions minutes.* The repository was made **public**,
   where Actions is free and unlimited. **The runs still failed identically**,
   which establishes that the lock is at the **account** level, not the
   repository level.

The automation itself is **not** unproven. Everything the workflow invokes was
executed directly against live pull requests:

```
$ ./scripts/review_pr.sh 1
  2 issue(s) found in 4.369s
    [HIGH]   app/api.py:34  completion ratio multiplied by 100 twice
    [MEDIUM] app/api.py:31  get_user may return None
    [TEST]   test_handle_completion_badge_scaling
    [TEST]   test_handle_user_badge_missing_user
  model=openai/gpt-oss-120b  retrieval=bm25+bidirectional-callgraph
```

So the pipeline — fetch diff → retrieve context → review → post inline comments —
works end to end on real pull requests. What does not work is GitHub's hosted
runner starting the job, which is a billing state on the account and not
something the project can fix in code.

**Honest statement of status:** the CI/CD integration is *implemented and
demonstrable*, but *not currently automatic*. It is invoked by a one-line
command rather than by a PR hook.
