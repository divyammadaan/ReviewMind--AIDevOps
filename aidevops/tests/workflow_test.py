"""End-to-end tests of the repair workflow: human approval, rejection, verification and rollback.

Needs the stack running (docker compose up) and a Groq key in ../.env for the AI cases.
    python tests/workflow_test.py

Nothing here is mocked: the AI cases call Groq, and the failure cases deploy genuinely bad patches
(one that compiles but does not fix the bug, one that hangs at start-up) to the real containers.
Faults are injected with report=false so no GitHub pull requests are opened.
"""
import time

import httpx

from common import (GW, check, has_discount_fix, break_service, docker, events, finish, golden_code, live_code, post, reset_all,
                    svc, wait)


def cart_total():
    """Real end-to-end read of the cart's pricing through the gateway."""
    httpx.delete(f"{GW}/cart/cart/wf-test", timeout=10)
    httpx.post(f"{GW}/cart/cart/wf-test/add", json={"product_id": 1, "qty": 2}, timeout=10)
    c = httpx.get(f"{GW}/cart/cart/wf-test", timeout=10).json()
    httpx.delete(f"{GW}/cart/cart/wf-test", timeout=10)
    return c["total"]


check(reset_all(), "stack healthy before testing")

# ---------------------------------------------------------------------------------------------
print("\n== 1. AI proposes, human approves, patch is applied and verified")
check(break_service("cart", "cart-discount"), "cart broken (discount applied as 1000%)")
check(cart_total() != 5398.2, "storefront really is wrong while broken", f"total={cart_total()}")
broken_code = live_code("cart")
check(post("/api/services/cart/fix", mode="ai").status_code == 200, "Fix with AI accepted")
check(wait(lambda: svc("cart")["repair"]["state"] == "pending_approval", 90), "state becomes Pending Approval")
p = svc("cart")["repair"]["proposal"]
check(bool(p) and has_discount_fix(p["diff"]) and p["tokens"] > 0, "a real diff is shown for review", str(p)[:200])
check(live_code("cart") == broken_code, "running service file NOT modified while pending")
time.sleep(5)
check(svc("cart")["status"] in ("broken", "down"), "service still broken while pending")
check(cart_total() != 5398.2, "storefront behaviour unchanged while pending")
check(post("/api/services/cart/fix", mode="ai").status_code == 409, "cannot request a second fix while one is pending")
check(post("/api/services/cart/approve").status_code == 200, "Approve & Apply accepted")
check(wait(lambda: svc("cart")["repair"]["state"] == "resolved", 60), "state becomes Resolved after verification")
check(svc("cart")["status"] == "healthy" and svc("cart")["bug"] is None, "service healthy, fault cleared")
check(cart_total() == 5398.2, "storefront pricing correct again", f"total={cart_total()}")
check(any("Verified and resolved" in e["msg"] for e in events()), "activity log records the resolution")

# ---------------------------------------------------------------------------------------------
print("\n== 2. Rejection discards the proposal without touching the service")
check(break_service("cart", "cart-discount"), "cart broken again")
broken_code = live_code("cart")
post("/api/services/cart/fix", mode="ai")
check(wait(lambda: svc("cart")["repair"]["state"] == "pending_approval", 90), "proposal pending")
check(post("/api/services/cart/reject").status_code == 200, "Reject accepted")
check(svc("cart")["repair"]["state"] == "rejected", "state becomes Rejected")
check(svc("cart")["repair"]["proposal"] is not None and svc("cart")["repair"]["proposal"]["diff"], "rejected diff kept for the record")
check(live_code("cart") == broken_code, "running service file untouched after rejection")
time.sleep(6)
check(svc("cart")["status"] in ("broken", "down"), "service still broken after rejection")
check(post("/api/services/cart/approve").status_code == 409, "a rejected proposal cannot be approved")
check(post("/api/services/cart/fix", mode="golden").status_code == 200, "operator restores known-good instead")
check(wait(lambda: svc("cart")["status"] == "healthy", 40) and svc("cart")["repair"]["state"] == "resolved", "service recovered")

# ---------------------------------------------------------------------------------------------
print("\n== 3. A patch that compiles but does not fix the bug is rolled back automatically")
check(break_service("catalog", "catalog-price"), "catalog broken (price in the wrong unit)")
bad_patch = live_code("catalog") + "\n# attempted fix that changes nothing\n"
r = httpx.post("http://localhost:9000/api/services/catalog/propose", json={"code": bad_patch}, timeout=20)
check(r.status_code == 200 and svc("catalog")["repair"]["state"] == "pending_approval", "manual patch held for approval")
check(post("/api/services/catalog/approve").status_code == 200, "approved")
check(wait(lambda: svc("catalog")["repair"]["state"] == "rolled_back", 60), "state becomes Rolled back (not Resolved)")
rb = svc("catalog")["repair"]["rollback"]
check(bool(rb) and "price" in rb["reason"] and rb["t"] > 0, "failure reason and timestamp recorded", str(rb))
check(rb["recovered"] is True and svc("catalog")["status"] == "healthy", "service healthy again after rollback")
check(live_code("catalog") == golden_code("catalog"), "live file is the last known-good version")
check(svc("catalog")["repair"]["state"] != "resolved", "unsuccessful patch is never marked resolved")
check(any("Verification failed" in e["msg"] for e in events()) and any("Rolled back" in e["msg"] for e in events()),
      "rollback event and reason appear in the activity log")

# ---------------------------------------------------------------------------------------------
print("\n== 4. A patch that hangs the service at start-up times out and is rolled back")
check(break_service("cart", "cart-overwrite"), "cart broken (overwrites quantity)")
hang = "import time\ntime.sleep(300)\n" + live_code("cart")
r = httpx.post("http://localhost:9000/api/services/cart/propose", json={"code": hang}, timeout=20)
check(r.status_code == 200, "hanging patch compiles, so it is held for approval")
post("/api/services/cart/approve")
check(wait(lambda: svc("cart")["repair"]["state"] == "rolled_back", 90), "verification times out, state becomes Rolled back")
rb = svc("cart")["repair"]["rollback"]
check(bool(rb) and "unreachable" in rb["reason"], "timeout reason recorded", str(rb))
check(svc("cart")["status"] == "healthy" and cart_total() == 5398.2, "service serves correct prices again")

# ---------------------------------------------------------------------------------------------
print("\n== 4b. A poisonous patch that ignores shutdown signals must not wedge the rollback")
check(break_service("cart", "cart-overwrite"), "cart broken again")
poison = ("import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\nsignal.signal(signal.SIGINT, signal.SIG_IGN)\n"
          "time.sleep(600)\n" + live_code("cart"))
check(httpx.post("http://localhost:9000/api/services/cart/propose", json={"code": poison}, timeout=20).status_code == 200,
      "poison patch compiles, so it is held for approval")
post("/api/services/cart/approve")
check(wait(lambda: svc("cart")["repair"]["state"] == "rolled_back", 90), "state becomes Rolled back")
check((svc("cart")["repair"]["rollback"] or {}).get("recovered") is True, "rollback RECOVERED the service even though it ignores SIGTERM",
      str(svc("cart")["repair"]["rollback"]))
check(wait(lambda: svc("cart")["status"] == "healthy", 15) and cart_total() == 5398.2, "service serving correct prices again")

# ---------------------------------------------------------------------------------------------
print("\n== 5. Input validation and guard rails")
r = httpx.post("http://localhost:9000/api/services/cart/propose", json={"code": "def broken(:"}, timeout=20)
check(r.status_code == 400, "a patch that does not compile is refused outright", r.text[:120])
check(post("/api/services/cart/approve").status_code == 409, "approve with nothing pending is refused")
check(post("/api/services/cart/reject").status_code == 409, "reject with nothing pending is refused")

check(break_service("catalog", "catalog-typo"), "catalog broken for the stale-proposal case")
httpx.post("http://localhost:9000/api/services/catalog/propose", json={"code": live_code("catalog") + "\n# x\n"}, timeout=20)
check(svc("catalog")["repair"]["state"] == "pending_approval", "proposal pending")
post("/api/services/catalog/fix", mode="golden")  # operator restores instead: the proposal must be discarded
wait(lambda: svc("catalog")["status"] == "healthy", 40)
check(post("/api/services/catalog/approve").status_code == 409, "a proposal discarded by a restore cannot be applied")

# ---------------------------------------------------------------------------------------------
print("\n== 6. Automation mode (auto_approve) still repairs end to end")
check(break_service("inventory", "inventory-keyerror"), "inventory broken")
check(post("/api/services/inventory/fix", mode="ai", auto_approve="true").status_code == 200, "fix with auto_approve")
check(wait(lambda: svc("inventory")["repair"]["state"] == "resolved", 90), "AI patch applied and verified")
check(any("Auto-approve is on" in e["msg"] for e in events()), "auto-approval is logged loudly, never silent")

# ---------------------------------------------------------------------------------------------
print("\n== 7. Break still works after a repair reworded the code (0.10 became 0.1)")
check(break_service("cart", "cart-overwrite"), "cart broken (overwrites quantity)")
reworded = golden_code("cart").replace("0.10", "0.1")
check(reworded != golden_code("cart"), "built an equivalent repair that is spelled differently")
httpx.post("http://localhost:9000/api/services/cart/propose", json={"code": reworded}, timeout=20)
post("/api/services/cart/approve")
check(wait(lambda: svc("cart")["repair"]["state"] == "resolved", 60), "the reworded repair is applied and verified")
check("0.10" not in live_code("cart"), "the live code no longer contains the original spelling")
check(break_service("cart", "cart-discount"), "the discount fault can still be injected (falls back to the original code as its base)")
check("total * 10" in live_code("cart"), "and it really is injected")
post("/api/services/cart/fix", mode="golden")
wait(lambda: svc("cart")["status"] == "healthy", 40)

reset_all()
finish()
