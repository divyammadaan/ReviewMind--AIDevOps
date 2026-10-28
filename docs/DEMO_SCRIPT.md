# ReviewMind — Demonstration Runbook

Roughly 8 minutes. Three acts: **the idea** → **the proof** → **the real thing**.

---

## BEFORE they walk in (do this 10 minutes early)

```bash
cd ~/ReviewMind_IDT
.venv/bin/uvicorn api.main:app --port 8000      # leave running in its own tab
```

Open these **four browser tabs**, in this order:

1. `http://127.0.0.1:8000/demo` — live review
2. `http://127.0.0.1:8000/` — dashboard
3. `https://github.com/Deadmaus07/ReviewMind/pull/3` — **unreviewed**, simple code (the live moment)
4. `https://github.com/Deadmaus07/ReviewMind/pull/1` — already reviewed, as backup

Have a **second terminal tab** open at `~/ReviewMind_IDT`, ready to type.

**Check:** `curl -s localhost:8000/api/health` should say `"key_present":true`.
Confirm you have internet. Then **stop touching it.**

---

## ACT 1 — The idea (2 min) · Tab 1

> "Code review catches bugs before they ship, but it's slow and inconsistent.
> ReviewMind reviews every pull request automatically. The difference from a
> normal linter is that it reads the *rest of the codebase* first."

Click **“Shows 8000% instead of 80%”**. Let them read it — four lines, no jargon.

> "This takes a student's average and shows it as a percentage, so it multiplies
> by 100. Completely reasonable. Nothing on this screen is wrong."

Tick **"Compare both"**. Click **Review**.

**Say nothing for 4 seconds. Let them watch it work.**

Then point at the two halves:

> "Top half is the AI shown only the change. It guesses — says the value might
> be missing. Wrong. Bottom half is the same AI, same model, same prompt, with
> one difference: it could read the other files first. It found the real bug —
> `average_marks` ALREADY returns a percentage, so multiplying by 100 turns 80%
> into 8000%."

Point at the bottom panel:

> "And here's *why* it knew. It fetched `average_marks` from `marks.py`. The
> green tag says `callee` — that's the function this change calls."

**That single screen is the whole project.** Don't rush it.

---

## ACT 2 — The proof (3 min) · Tab 2

> "One example isn't evidence. So I planted 14 known bugs and tested three
> reviewers on the same data."

Point at the three bars.

> "Traditional tools, about 2 in 10. AI without context, about 8. AI with
> context, 10 out of 10."

Scroll to **the decisive comparison**:

> "But I had to be careful. Bugs I made by *deleting* code gave the answer away
> — the deleted line said what the rule was. Only these four, where I *added*
> wrong code, test it honestly. There: 0, 2, and 4 out of 4. Context doubled it."

**Say the limitation yourself, before they ask:**

> "That's four cases. It's a two-case difference. The direction is clear but I'm
> not claiming statistical significance."

Scroll to **the three red cards**:

> "The most useful part of this project was finding out my own experiment was
> wrong, three times."

Then, briefly — **do not read them out**:

> "First it said my approach was worse; that was the network failing, not a
> result. Then it said no difference; I'd accidentally made the answer visible
> in the question. Then still no difference; I was giving credit for pointing at
> the right line while describing the wrong bug. Fixed all three."

---

## ACT 3 — The real thing (3 min) · Tab 3

> "Finally — this isn't a toy. It runs on real pull requests."

Show **PR #2**. Scroll the code change.

> "Someone added a function that makes a short badge from a student's name.
> Four lines. Looks completely fine."
>
> "But look at `find_student` — it's in a different file, and it returns
> *nothing* when the student doesn't exist. You can't read a name out of
> nothing. So this crashes."

Point out there are **no comments yet.** Then in the terminal:

```bash
./scripts/review_pr.sh 3
```

While it runs (about 5 seconds), say:

> "It's pulling the diff from GitHub, retrieving related code, reviewing, and
> posting back."

**Refresh the PR in the browser.** The comment is there, on the line.

> "That's an inline comment on the exact line, a summary, and a suggested test."

Then switch to **tab 4 (PR #1)**:

> "And here's the same thing next to CodiumAI's PR-Agent, which is an industry
> tool. It found the same defect independently — and it reported 'no relevant
> tests', while mine supplied one."

---

## IF ASKED

**"Is this automatic?"**
> "The workflow is committed and the pipeline is proven — you just watched it
> review a real pull request. GitHub Actions is locked on my account for a
> billing reason, so the hosted runner never starts the job. I verified that by
> making the repository public, where Actions is free and unlimited — it still
> failed with the same annotation, which shows the lock is on the account, not
> the repo. So I trigger it with one command instead. The automation works;
> GitHub won't run it for me."

*Do not claim it fires automatically. It does not. The error annotation is
visible in the Actions tab if they want to see it — showing them is better than
hedging.*

**"Did you write all this?"**
> "I used AI assistance to build it. What I can do is explain and defend every
> design decision — ask me about any of them."

**"Why not Sweep.dev / Sourcegraph?"**
> "Sweep.dev shut down — their domain doesn't even resolve any more, and their
> README says they've moved to a JetBrains plugin. So I built the capability
> myself. Sourcegraph's container is Intel-only and my machine is ARM, so I use
> their hosted instance. Both documented with evidence."

**"What's the weakest part?"**
> "Sample size. Fourteen bugs, four in the decisive comparison. And I built both
> the system and its test, which is a conflict of interest. I mitigated it by
> including bug types I expected to lose on — and the traditional tool beats me
> 3 out of 3 on those."

---

## IF SOMETHING BREAKS

| Problem | Do this |
|---|---|
| Page won't load | Restart: `.venv/bin/uvicorn api.main:app --port 8000` |
| Review hangs / rate-limited | Switch to tab 4 — PR #1 already has real comments |
| Want a second run | Use PR #1 or #2, never #3 — keep #3 clean |
| No internet | Dashboard works offline. Use tabs 2 and 4. |
| Everything fails | Talk through tab 2 and `docs/LIMITATIONS.md`. The research is the marks. |

**Rate limit:** ~200,000 tokens/day, each review ~2,000. Don't rehearse more than
a few times on demo day. **Never run the full experiment live** — it takes 20
minutes and will rate-limit mid-presentation.
