# ReviewMind — Team Responsibility Matrix

> **⚠️ ACTION REQUIRED FROM THE STUDENT.**
> This is a **template**. I do not know your team members, so I have not invented
> them. Fabricating a team roster would be dishonest and trivially exposed in a
> viva. Fill in the real names and contributions, or delete the extra rows if you
> are working solo.
>
> Your handout says projects run in teams of 3–5 with clear role distribution,
> and A2 criterion **C10 is "Individual Contribution"**. Faculty may ask each
> member what they personally built.

## Roles

| Member | Role | Owned components | Contribution |
|---|---|---|---|
| **Manan Sharma** | *(fill in)* | *(fill in)* | *(fill in)* |
| *(name)* | | | |
| *(name)* | | | |

## Component ownership — assign each to a real person

| Component | Files | Owner |
|---|---|---|
| Evaluation corpus & defect catalogue | `experiments/bugs/`, `experiments/corpus/` | *(assign)* |
| Dataset generator | `experiments/seed_bugs.py` | *(assign)* |
| Chunking (`ast`) | `reviewmind/parsing/chunker.py` | *(assign)* |
| Retrieval — BM25 | `reviewmind/retrieval/bm25.py` | *(assign)* |
| Retrieval — call-graph | `reviewmind/retrieval/callgraph.py`, `graph.py` | *(assign)* |
| Static baseline (Arm A) | `reviewmind/arms/static_arm.py` | *(assign)* |
| LLM arms (B, C) | `reviewmind/arms/llm_arm.py`, `review/` | *(assign)* |
| Scoring & metrics | `experiments/scoring.py`, `bugs/concepts.py` | *(assign)* |
| Experiment runner / analysis | `experiments/run_experiment.py`, `analyze.py` | *(assign)* |
| LangChain integration | `reviewmind/qa/langchain_bot.py` | *(assign)* |
| Code Q&A bot | `reviewmind/qa/bot.py` | *(assign)* |
| GitHub Actions + PR posting | `.github/workflows/`, `reviewmind/github/` | *(assign)* |
| Issue→PR automation | `reviewmind/automation/` | *(assign)* |
| Sourcegraph navigation | `reviewmind/navigation/`, `experiments/sourcegraph_navigation.py` | *(assign)* |
| CodiumAI test generation | `tools/codium/` | *(assign)* |
| Documentation | `docs/` | *(assign)* |

## Note on AI assistance — declare this honestly

This project was developed with AI assistance (Claude) for implementation,
debugging and documentation. Your handout's academic-honesty clause makes
misrepresentation risky, and your criteria require you to **defend** the work
personally.

The defensible position is not "I wrote every line" but **"I can explain and
justify every design decision, and here is the evidence for each."** Use
`docs/VIVA_PREP.md`. Check with your faculty what disclosure they expect.
