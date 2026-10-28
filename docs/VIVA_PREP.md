# ReviewMind — Viva Preparation

Your criteria state: *"Each student is expected to have a complete understanding
of the entire project and should be able to explain and defend the research
question, methodology, implementation, experiments, results, limitations, and
conclusions."*

**Do not memorise this. Understand the three stories in §2 — they are the
strongest material you have, and they are strongest when told in your own words.**

---

## 1. Thirty-second summary

> "ReviewMind reviews pull requests by first retrieving related code from
> elsewhere in the repository, then asking an LLM to judge the change. I built an
> experiment to test whether that retrieval actually helps: three arms — static
> analysis, LLM with only the diff, and LLM with retrieved context — on 14
> defects I injected into a small app so I knew the exact ground truth. The LLM
> beat static analysis decisively. Retrieval looked useless at first, and finding
> out why it only *looked* useless is the most interesting part of the project."

---

## 2. The three stories — your strongest material

### Story 1 — "RAG is worse" was a network artifact

The first full run said RAG scored 80% against diff-only's 100%. Two of Arm C's
API calls had hit HTTP 429 rate limits. A failed call returns zero issues, which
my scorer correctly counted as misses.

**The point to make:** Arm C has the largest prompts, so throttling hit it first
— the bias ran *against* my own method. If I had trusted the table I would have
reported the opposite of the truth. Fixed with a token-budget throttle.

*If asked "how do you know your other results aren't artifacts too?"* — Honest
answer: I don't, fully. That's why every run records a non-fatal error column and
why I read it before the results table. It's also in `LIMITATIONS.md` §2.3.

### Story 2 — my dataset leaked its own answers

With rate limits fixed, both LLM arms hit 100% — an apparent null result for
retrieval. But diff-only was solving cross-file defects it shouldn't have been
able to see. Its own explanation gave it away:

> *"violating the function's contract of returning `"unknown"` for missing users"*

Every defect was made by **deleting correct code**, so the deleted lines showed
up as `-` lines in the diff — and those lines *state the contract being
violated*. I was handing the model the answer key.

**Fix:** 4 *addition-mode* defects that add wrong code and delete nothing,
verified programmatically to contain zero `-` lines.

*If asked "why didn't you see that coming?"* — I flagged each defect as
cross-file based on its *nature*, but what the diff revealed depended on the
*injection method*. Those two came apart and I only caught it by reading the
model's reasoning rather than just its score.

### Story 3 — my metric measured location, not understanding

Diff-only *still* scored 4/4. Because credit went to any issue within ±3 lines,
it was being rewarded for flagging the right line while describing a different
bug:

| case | diff-only said | RAG said |
|---|---|---|
| `add_double_scale_001` | *"if completion_ratio raises… no error handling"* ✗ | *"already returns a percentage; ×100 gives 5000%"* ✓ |
| `add_ignored_return_001` | *"catches all Exception types…"* ✗ | *"boolean return ignored; failure reported as success"* ✓ |

**The deeper point:** the leniency systematically favoured the arm with *less*
context, because a vague nearby guess is exactly what a context-starved reviewer
produces. Adding a concept rubric separated them **50% vs 100%**.

---

## 3. Questions you should expect

**"What is your research question?"**
Does repository-wide retrieved context measurably improve an LLM's defect
detection, versus the diff alone and versus static analysis? Three sub-questions:
RQ1 LLM vs static, RQ2 RAG vs diff-only, RQ3 the cost.

**"Why is that falsifiable?"**
Because it can answer *no* — and it nearly did twice. Retrieval can hurt by
adding distracting context, and static analysis can win on pattern defects. I
pre-committed to reporting whichever happened.

**"Why should I believe your baseline is fair?"**
Three of my seven defect classes were chosen for static analysis to **win**, and
it scores 3/3 on them. I ran ruff with bug-oriented rule families, not defaults,
and bandit at low thresholds. I also *deleted* a case contaminated in my own
favour — `add_null_deref_002` originally sat where ruff flagged `F821` for the
wrong reason.

**"Is your result statistically significant?"**
**No, and I don't claim it is.** n=14 overall, n=4 in the decisive cell — a
two-case difference. I report Wilson 95% CIs, which are wide and overlapping.
The *direction* is defensible; significance is not.

**"Why not tree-sitter, as your proposal said?"**
For Python, `ast` is the language's own parser and can't disagree with the
interpreter about what a function is, and it has no wheel-availability risk on
Python 3.14. tree-sitter sits behind the same interface for other languages.

**"Where's Sourcegraph?"**
The `sourcegraph/server` image is **amd64-only** and my VM is arm64 (Apple
Silicon). I verified that with `docker manifest inspect`. I use the official
`src` CLI against the public instance — 6 query types, all returning hits — and
I'm explicit that sourcegraph.com can't index my local corpus, so Sourcegraph is
**not part of the measured pipeline**. My own retriever does that, and it's
measured.

**"Where's Sweep.dev?"**
It shut down. `sweep.dev` doesn't resolve in DNS while github.com and
sourcegraph.com resolve fine from the same machine; their README announces a
pivot to JetBrains. I implemented the capability it provided — label an issue,
get an AI-generated PR — with a default-deny path allow-list so the automation
can't rewrite its own CI pipeline.

**"What would you do with another month?"**
Fix the biggest weakness first: n. More defects, and real bug-fix PRs instead of
only synthetic ones. Then variance runs, a second model family, and a hand-audit
to turn my precision lower bound into a real number.

**"What's the weakest part?"**
Sample size, and that I built both the system and its evaluation. Both failures I
found were ones that made my method look *bad*, which gave me a motive to dig. I
can't rule out symmetric errors that flattered it and so went uninvestigated.

---

## 4. Numbers worth knowing

- **3 arms, 14 defects, 7 classes** (10 deletion-mode, 4 addition-mode)
- **static 21.4% · diff-only 78.6% · RAG 100%** semantic recall
- Decisive cell: **static 0/4 · diff-only 2/4 · RAG 4/4**
- Retrieval ablation: **4/4 at k=1** vs BM25's k=6
- RAG costs **+48% tokens** (2,219 vs 1,500/case)
- Static analysis: **0/5** on semantic defect classes, **3/3** on pattern ones
- CodiumAI cover-agent: **75% → 100%** coverage
- Groq limits: **8k tokens/min, 200k/day** — the binding constraint

## 5. Live demo — what to show, and what not to

**Show:** one PR reviewed end-to-end; the retrieval ablation (no API needed); the
static arm (no API needed); the saved results tables; Sourcegraph queries.

**Do NOT run the full experiment live.** It needs ~30k tokens against a 200k/day
cap, takes ~20 minutes under throttling, and a 429 mid-demo looks like failure.
Run it beforehand and present `results/`.
