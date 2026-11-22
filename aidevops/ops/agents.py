"""The agentic repair workflow: small specialised LLM agents that each do one job.

  Triage    reads the failing check, logs and trace and says what is probably wrong (no code)
  Fixer     writes the patch (the existing fixer, now guided by the triage hypothesis)
  Reviewer  judges the patch BEFORE a human sees it: does it fix the defect in general, or only special-case
            the example in the failing check? It can send the patch back for one revision
  Verifier  not an LLM: the deterministic compile + health-probe check after approval, with rollback

Triage and Reviewer run on the cheaper model first, so a repair costs a few thousand tokens, not many. Agents
are optional: if one fails or returns something unusable, the pipeline carries on without it.
"""
import json
import time

import fixer

CHEAP = ["openai/gpt-oss-20b", "openai/gpt-oss-120b"]

TRIAGE_SYSTEM = (
    "You are the triage agent on an SRE team. You get one failing health check, recent logs, optional trace "
    "evidence and the source of one small FastAPI microservice. Say what is probably wrong and where. Do NOT "
    "write code. Reply with JSON only: {\"summary\": \"at most 2 sentences\", \"suspect\": \"the function or "
    "line to look at\", \"general_rule\": \"the rule the code should follow in general, not just for the "
    "example values in the check\"}"
)

REVIEW_SYSTEM = (
    "You are the code-review agent on an SRE team. You get a failing health check, the service's source before "
    "the change, and the proposed diff. Judge the patch: does it fix the underlying defect in general, or does "
    "it only special-case the example values from the health check, hard-code constants, or change unrelated "
    "behaviour? Reply with JSON only: {\"verdict\": \"approve\" or \"revise\", \"concerns\": [\"short concern\"], "
    "\"special_cases_examples\": true or false}. Approve only when the change is minimal and general."
)


def _json(text: str) -> dict:
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        return {}
    try:
        return json.loads(text[a:b + 1])
    except ValueError:
        return {}


def _run(keys, system, prompt, max_tokens=350):
    t0 = time.time()
    text, meta = fixer.ask(keys, prompt, system=system, max_tokens=max_tokens, raw=True, models=CHEAP)
    return _json(text), meta, round(time.time() - t0, 1)


def triage(keys, svc, symptom, logs, hint, code):
    data, meta, secs = _run(keys, TRIAGE_SYSTEM,
                            f"Service: {svc}\nFailing health check:\n{symptom}\n\nRecent logs:\n{logs.strip() or '(none)'}\n"
                            + (f"\nTrace evidence: {hint}\n" if hint else "") + f"\nSource:\n{code}")
    if not data.get("summary"):
        raise RuntimeError("triage returned no summary")
    return {"agent": "Triage", "summary": str(data["summary"])[:300], "suspect": str(data.get("suspect", ""))[:160],
            "general_rule": str(data.get("general_rule", ""))[:300], "tokens": meta["tokens"], "seconds": secs,
            "model": meta["model"]}


def review(keys, svc, symptom, before, diff_text):
    data, meta, secs = _run(keys, REVIEW_SYSTEM,
                            f"Service: {svc}\nFailing health check:\n{symptom}\n\nSource before the change:\n{before}\n\n"
                            f"Proposed diff:\n{diff_text}")
    verdict = str(data.get("verdict", "")).lower()
    if verdict not in ("approve", "revise"):
        raise RuntimeError("reviewer returned no verdict")
    concerns = [str(c)[:200] for c in (data.get("concerns") or [])][:4]
    return {"agent": "Reviewer", "verdict": verdict, "concerns": concerns,
            "special_cases_examples": bool(data.get("special_cases_examples")),
            "summary": "Approved: minimal and general." if verdict == "approve" else "Asked for a revision: " + "; ".join(concerns),
            "tokens": meta["tokens"], "seconds": secs, "model": meta["model"]}
