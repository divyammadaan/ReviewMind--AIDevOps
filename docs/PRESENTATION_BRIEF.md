# ReviewMind — Presentation Brief

Everything needed to present and defend the project. Read Part 1 once, then
Part 2 slide by slide. Part 3 is for the hard questions.

---

# PART 1 — THE PROJECT, IN DEPTH

## The one-sentence version

> ReviewMind reviews GitHub pull requests by first retrieving related code from
> elsewhere in the repository, which lets it catch defects that are only wrong
> because of something defined in a different file.

## The problem

Code review catches bugs before they ship, but it is slow and depends on who is
free. Automated tools exist — linters like ruff and bandit — but they only match
**patterns in the code they are shown**. They read the changed lines and nothing
else.

That is enough for "you wrote a password in the source" or "you used a bare
except". It is not enough for a change that is wrong because of a *contract*
defined somewhere else.

## The example that carries the whole project

A developer adds one line to a checkout page:

```python
payable = amount * percent
```

Nothing is wrong with that line. It reads naturally: take the amount, apply the
percent.

But `discount_percent()` lives in `pricing.py` — a **different file** — and it
returns **20**, meaning *twenty percent off*. It is not a multiplier.

So a ₹1,000 order is billed **₹20,000** instead of ₹800.

- A linter sees only the changed line → finds nothing
- An AI shown only the diff → guesses, usually wrongly
- ReviewMind fetches `pricing.py` first → gets it right

**That gap is the entire project.**

## How it works, end to end

1. **Trigger** — a pull request is opened. (Workflow committed; we invoke it
   directly because Actions is billing-locked on the account.)
2. **Fetch** — the diff is pulled from the GitHub API.
3. **Parse** — Python's own `ast` module splits the repository into pieces:
   one per function, class, and module preamble.
4. **Retrieve** — the core step. Pick roughly six pieces most likely to matter:
   - **what the change calls** — follow `discount_percent()` to its definition
   - **what calls the change** — in case the change breaks its users
   - **keyword matching** — pieces sharing unusual words with the change
5. **Prompt** — assemble a reviewer persona, the diff, the retrieved pieces, and
   a required JSON output format.
6. **Review** — the LLM (`gpt-oss-120b` via Groq, temperature 0) returns located
   issues plus suggested tests.
7. **Post** — issues become inline comments on the exact lines, plus a summary.

## Why retrieval is the interesting part

Everything except step 4 is plumbing. The hard question is: *given thousands of
files, which six do you show the model?*

Keyword matching alone is not enough. A diff that deletes a null check shares
vocabulary with the code that **calls** it, but not necessarily with the code it
**calls** — and the callee is where the contract lives. We measured exactly this
failure and fixed it by following the call graph in both directions.

Measured outcome: with both directions, the file that matters is the **first**
result. Keyword matching alone needs its top **six**.

## What we proved

14 defects injected by script into a test service, 7 categories, exact ground
truth. Three reviewers on identical data, marked by one scorer:

| Reviewer | Correctly described the defect |
|---|---|
| Traditional linters | **21%** |
| AI, shown only the diff | **79%** |
| AI + repository context | **100%** |

Fair-test subset (defects created by *adding* wrong code, which leak nothing):
**linters 0/4 · AI alone 2/4 · AI with context 4/4**

## The honest limitation

14 defects overall, 4 in the decisive comparison. That is a two-case difference.
**We claim a direction, not statistical significance.** Expanding it is the
end-term work.

---

# PART 2 — SLIDE BY SLIDE

## Slide 1 — Title

**Say:** "ReviewMind — an AI code reviewer that reads the rest of your codebase
before judging a change. Built by Divyam and myself for the mid-term."

**Likely question:** *"What makes it different from existing tools?"*
> "Existing tools only read the lines that changed. Ours goes and reads the
> related files first. I'll show you a bug that's impossible to catch otherwise."

## Slide 2 — Problem and objective

**Say:** "Code review is slow and depends on who's available. Linters help, but
they only match patterns in the code they're shown. Here's a bug they cannot
catch." Then walk the ₹20,000 example.

**Likely question:** *"Why can't a linter catch this?"*
> "Because nothing about that line is wrong. You'd have to open `pricing.py` and
> know that `discount_percent` returns 20 meaning twenty percent off. A linter
> doesn't open other files."

**Likely question:** *"Is this a realistic bug?"*
> "Unit-confusion bugs are common — percentage versus fraction, rupees versus
> paise, seconds versus milliseconds. They're dangerous because they don't
> crash; they just produce wrong numbers."

## Slide 3 — Methodology

**Say:** "Pull request comes in. We fetch the diff, split the repository into
functions, retrieve the handful that matter, build a prompt, ask the model, and
post comments back. Step three is the one that matters — everything else is
plumbing."

**Likely question:** *"Why `ast` and not tree-sitter?"* (we proposed tree-sitter)
> "`ast` is Python's own parser, so it can't disagree with Python about what a
> function is, and it has no installation risk. tree-sitter is the right choice
> for multiple languages, which is future work."

**Likely question:** *"Why six pieces?"*
> "It's a budget. Too few and you miss the relevant file; too many and you blow
> the model's context limit and cost. Six was enough for every case we tested."

## Slide 4 — How retrieval works

**Say:** "To judge that line you have to answer one question — what does
`discount_percent` actually return? The answer isn't in the change. It's in
another file. So we go and read it."

**Likely question:** *"How does it know which file to read?"*
> "It follows what the code calls. The change calls `discount_percent()`, so we
> find where that's defined and fetch it. We also go the other way — what calls
> the changed code — in case the change breaks its users."

**Likely question:** *"What if the right file isn't retrieved?"*
> "Then the review is no better than diff-only. That's why we measured retrieval
> separately from the AI: with both directions the right file is first; keyword
> matching alone needs its top six."

## Slide 5 — Supporting tools

Keep this brief. One sentence each:
- **FastAPI + Docker** — the web app, packaged to run with one command
- **CodiumAI** — AI test generation; took coverage from 75% to 100%
- **Sourcegraph** — code search across whole repositories
- **Standard linters** — the baseline we compared against

**Likely question:** *"Why do you need a baseline?"*
> "Otherwise 'we found 14 bugs' means nothing. Fourteen out of what? Better than
> what? The linters are what you'd use if our project didn't exist."

## Slide 6 — What was built

**Say:** "Review engine, a Q&A bot over the same index, two GitHub workflows, a
web application, the experiment harness, and it's all containerised."

**Likely question:** *"What's the Q&A bot?"*
> "Same retrieval, different job. Instead of reviewing a change it answers
> questions about the codebase, and cites which files the answer came from.
> I'll show you." (Then demo it — it's on the control panel.)

**Likely question:** *"What does 'never executes pull-request code' mean?"*
> "A deliberate safety decision. We parse the code and send text to a model; we
> never run it. A reviewer bot that executes untrusted code from a pull request
> is a serious security hole."

## Slide 7 — Results

**Say:** "14 defects, three reviewers, same data, same scorer. Linters got 21%,
the AI alone 79%, the AI with repository context 100%."

**Likely question:** *"What does 21% mean exactly?"*
> "Of 14 defects, it correctly described three. We only count a hit if the
> report describes the actual defect — not just points near the right line."

**Likely question:** *"Isn't 100% suspicious?"*
> "On this dataset, yes — it suggests the dataset is too easy, not that the tool
> is perfect. That's why the fair-test subset matters more, and why the next
> step is harder, real-world defects."

## Slide 8 — Comparison

**Say:** "Linters caught 3 of 3 defects of the kind they're designed for and 0
of 8 that need understanding meaning. And an industry tool, CodiumAI's
PR-Agent, independently flagged the same defect on the same pull request."

**Likely question:** *"Did you pick defects your tool would win on?"*
> "We deliberately included categories where the linters should win — hardcoded
> passwords, bare exceptions, SQL injection — and they scored 3 out of 3 on
> those. If we'd only picked semantic bugs the comparison wouldn't be credible."

## Slide 9 — Challenges

**Say:** Walk two or three, not all six. The strongest are Sweep.dev and the
early wrong results.

**Likely question:** *"Why no Sweep.dev? It's in the rubric."*
> "The company shut down. Their domain doesn't resolve in DNS, while GitHub and
> Sourcegraph resolve fine from the same machine, and their README says they've
> moved to a JetBrains plugin. So I built the capability myself — label an
> issue, the bot opens a pull request with a fix, restricted so it can't touch
> CI config or secrets."

**Likely question:** *"What was hardest?"*
> "Finding out my own results were wrong, three times. Network failures were
> being counted as missed defects. Then the test was leaking its own answers,
> because defects made by deleting code left the deleted line visible. Then the
> metric was rewarding the right line rather than the right explanation. All
> three found by measurement."

## Slide 10 — Demo and next steps

Then run the demo. See Part 4.

---

# PART 3 — THE HARD QUESTIONS

**"Did you write all this yourself?"**
> "We used AI assistance to build it. What we can do is explain and defend every
> design decision — ask us about any of them."
*Do not claim otherwise. Be ready to explain any file.*

**"Your sample size is tiny."**
> "Agreed — 14 defects, and only 4 in the decisive comparison. That's a
> two-case difference. I claim a direction, not significance, and it's stated on
> the slide. Expanding it with real bug-fix pull requests is the end-term work."

**"You built the system and the test that judges it. Isn't that biased?"**
> "Yes, that's a real conflict. Three mitigations: the defect categories were
> fixed before any run; we deliberately included categories where the baseline
> should win, and it did, 3 out of 3; and we removed a test case that was unfair
> in our favour. The deeper issue is that the three errors I found all made my
> method look worse, which gave me a motive to dig. Errors that flattered it
> might have gone unchecked."

**"Why isn't the GitHub automation running automatically?"**
> "GitHub Actions is locked on my account for a billing reason — you can see the
> error in the Actions tab. I verified it's account-level, not repository-level,
> by making the repo public where Actions is free; it failed identically. The
> workflow is committed and the pipeline works — I trigger it with one command."

**"Why did you use X instead of Y?"**
Four substitutions, each with a reason:
- **`ast` instead of tree-sitter** — Python's own parser, no install risk
- **keyword + call-graph instead of a vector database** — deterministic and
  reproducible, which matters more in an experiment; the embedding version
  exists but is unmeasured, so we make no claim about it
- **`gpt-oss-120b` instead of Llama 3.3** — Groq retired Llama 3.3 mid-project
- **public Sourcegraph instead of self-hosted** — the image is Intel-only, our
  machine is ARM

**"What would you do differently?"**
> "Design the dataset more carefully from the start. Creating defects by
> deleting code seemed natural, but it leaked the answer into the diff and cost
> me a round of invalid results."

**"What's the weakest part?"**
> "Sample size, and that we evaluated our own work. Both are stated on the
> slides."

---

# PART 4 — THE DEMO

**Before they arrive:** both containers running; four tabs open —
control panel, shop page, PR #8, dashboard. Demo state **broken**.

1. **Shop page** — "₹1,000 order, gold customer, 20% off. It's charging ₹20,000."
2. **PR #8** — "This one-line change caused it."
3. **Control panel → Review PR** — ~5s. "It's fetching the real pull request,
   retrieving related code, reviewing, posting back."
4. **PR tab → refresh** — "Inline comment on the exact line."
5. **Control panel → Fix it** — ~3s. "It wrote that fix just now."
6. **Shop page → refresh** — "₹800. Correct."
7. **Ask the codebase** — click a preset question. "Same retrieval, answering
   questions, citing its sources."

**If something fails:** PR #8 already has real comments — talk through those.
The dashboard works without any network.

**Do not** run the full experiment live. It takes ~20 minutes and will hit rate
limits.
