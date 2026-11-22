"""End-to-end tests of the agentic repair workflow against the real stack and the real LLM (Groq).

    python tests/agents_test.py

Needs Groq keys in ../.env. Nothing is mocked: the Triage, Fixer and Reviewer agents are real model calls.
Faults are injected with report=false so no GitHub pull requests are opened.
"""
import httpx

from common import (OPS, break_service, check, finish, has_discount_fix, golden_code, live_code, post, reset_all, svc, wait)


def settings(**params):
    return httpx.post(f"{OPS}/api/settings", params=params, timeout=10).json()


def review(service, code):
    r = httpx.post(f"{OPS}/api/services/{service}/review", json={"code": code}, timeout=60)
    return r.status_code, r.json()


check(reset_all(), "stack healthy before testing")
settings(agentic="true", canary_enabled="false", watchdog="false")

# ---------------------------------------------------------------------------------------------
print("\n== 1. The Reviewer agent judges patches on their merits")
check(break_service("payments", "payments-pending"), "payments broken (charges never approved)")
broken = live_code("payments")
overfit = broken.replace('return {"status": "pending", "txn_id"',
                         'status = "approved" if c.amount <= 100 else "pending"\n    return {"status": status, "txn_id"', 1)
overfit = overfit.replace('"status": status, "txn_id"', '"status": status, "txn_id"')
check(overfit != broken and "amount <= 100" in overfit, "built the overfitted patch (special-cases the example amount 100)")
code, rv = review("payments", overfit)
check(code == 200 and rv["verdict"] == "revise", "Reviewer asks for a revision of the overfitted patch", str(rv)[:300])
print("   reviewer said:", rv.get("summary", "")[:200].replace("\n", " "), flush=True)
check(bool(rv.get("concerns")) and rv["tokens"] > 0, "it gives concrete concerns and reports its token cost")
code, rv = review("payments", golden_code("payments"))
check(code == 200 and rv["verdict"] == "approve", "Reviewer approves the correct, minimal fix", str(rv)[:300])
code, rv = review("payments", "def broken(:")
check(code == 400, "a patch that does not compile is refused before any agent is called")
post("/api/services/payments/fix", mode="golden")
wait(lambda: svc("payments")["status"] == "healthy", 40)

# ---------------------------------------------------------------------------------------------
print("\n== 2. A real fault goes through Triage -> Fixer -> Reviewer before a human sees it")
check(break_service("cart", "cart-discount"), "cart broken (discount applied as 1000%)")
cart_before = live_code("cart")
check(post("/api/services/cart/fix", mode="ai").status_code == 200, "Fix with AI accepted")
check(wait(lambda: svc("cart")["repair"]["state"] == "pending_approval", 120), "a proposal is waiting for approval")
p = svc("cart")["repair"]["proposal"]
steps = p["agents"]
names = [a["agent"] for a in steps]
print("   agent timeline:", names, flush=True)
check(names[0] == "Triage" and "Fixer" in names and names[-1] in ("Reviewer", "Fixer"), "the agents ran in the right order", str(names))
check("Reviewer" in names, "the Reviewer agent checked the patch")
check(all(a["tokens"] > 0 for a in steps) and sum(a["tokens"] for a in steps) == p["tokens"],
      "every agent reports its tokens, and they add up to the proposal total", f"{[a['tokens'] for a in steps]} vs {p['tokens']}")
check(has_discount_fix(p["diff"]), "the final patch is the correct fix (multiplies by 10%)", p["diff"][-120:])
check(len(steps[0]["summary"]) > 20, "Triage explained what is wrong in words")
print("   triage said:", steps[0]["summary"][:200], flush=True)
check(live_code("cart") == cart_before and wait(lambda: svc("cart")["status"] in ("broken", "down"), 15),
      "nothing was applied: the service file is unchanged and it is still broken, awaiting a human", svc("cart")["status"])
check(svc("cart")["tokens"] >= p["tokens"], "tokens are charged to the service, including the agents'")
post("/api/services/cart/reject")

# ---------------------------------------------------------------------------------------------
print("\n== 3. With agents off, a single Fixer does the job (as before)")
settings(agentic="false")
check(post("/api/services/cart/fix", mode="ai").status_code == 200, "Fix with AI accepted (agents off)")
check(wait(lambda: svc("cart")["repair"]["state"] == "pending_approval", 90), "a proposal is waiting")
names = [a["agent"] for a in svc("cart")["repair"]["proposal"]["agents"]]
check(names == ["Fixer"], "only the Fixer ran", str(names))
post("/api/services/cart/reject")
post("/api/services/cart/fix", mode="golden")
wait(lambda: svc("cart")["status"] == "healthy", 40)

settings(agentic="true")
reset_all()
finish()
