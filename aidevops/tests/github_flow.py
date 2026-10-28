"""End-to-end test of the incident-PR flow against tests/mock_github.py.

Needs: the stack up with tests/docker-compose.mock.yml, and `python tests/mock_github.py` running.
    python tests/github_flow.py
"""
import sys
import time

import httpx

OPS, GH = "http://localhost:9000", "http://localhost:9999"


def svc(name):
    return {s["name"]: s for s in httpx.get(f"{OPS}/api/state", timeout=10).json()["services"]}[name]


def wait(pred, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(1.5)
    return False


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        sys.exit(1)


httpx.post(f"{OPS}/api/reset")
check(wait(lambda: svc("cart")["status"] == "healthy", 40), "stack healthy")
check(httpx.get(f"{OPS}/api/state").json()["github"]["enabled"], "GitHub reporting enabled")

httpx.post(f"{OPS}/api/services/cart/break", params={"bug_id": "cart-discount"}).raise_for_status()
check(wait(lambda: (svc("cart")["pr"] or {}).get("state") == "open", 60), "incident PR opened")
pr = svc("cart")["pr"]
state = httpx.get(f"{GH}/_state").json()
p = state["prs"][str(pr["number"])]
check("health probe" in p["body"] and "5398.2" in p["body"], "PR body carries the failing probe")
branch_file = list(state["branches"][p["head"]].values())[0][0]
check("total * 10" in branch_file, "branch holds the broken code")

check(wait(lambda: len(httpx.get(f"{GH}/_state").json()["prs"][str(pr["number"])]["reviews"]) == 1, 40),
      "review comment posted")
rv = httpx.get(f"{GH}/_state").json()["prs"][str(pr["number"])]["reviews"][0]
c = rv["comments"][0]
src = branch_file.splitlines()
check(c["path"] == "aidevops/golden/cart/main.py" and "total * 10" in src[c["line"] - 1],
      f"comment is on the faulty line ({c['line']})")
print("   review text:", c["body"][:200].replace("\n", " "), flush=True)

httpx.post(f"{OPS}/api/services/cart/fix", params={"mode": "ai"}).raise_for_status()
check(wait(lambda: svc("cart")["status"] == "healthy", 60), "service repaired")
check(wait(lambda: svc("cart")["pr"]["state"] == "closed", 40), "PR closed after the fix")
state = httpx.get(f"{GH}/_state").json()
p = state["prs"][str(pr["number"])]
fixed = list(state["branches"][p["head"]].values())[0][0]
check("total * 10" not in fixed and "0.10" in fixed, "fix committed to the branch")
check(p["state"] == "closed" and any("Resolved" in x for x in p["comments"]), "PR has a Resolved comment and is closed")

# reporting off: no PR
httpx.post(f"{OPS}/api/github", params={"enabled": "false"})
n_before = len(httpx.get(f"{GH}/_state").json()["prs"])
httpx.post(f"{OPS}/api/services/catalog/break", params={"bug_id": "catalog-typo"})
time.sleep(12)
check(len(httpx.get(f"{GH}/_state").json()["prs"]) == n_before, "no PR while reporting is off")
httpx.post(f"{OPS}/api/services/catalog/fix", params={"mode": "golden"})
httpx.post(f"{OPS}/api/github", params={"enabled": "true"})
print("\nall checks passed")
