"""End-to-end tests of canary releases against the real stack.

    python tests/canary_test.py

Real traffic goes through the real gateway to real stable and canary containers. Faults are injected with
report=false so no GitHub pull requests are opened. No LLM is used: patches are supplied by hand.
"""
import threading
import time
from collections import Counter

import httpx

from common import (GW, OPS, break_service, check, docker, finish, golden_code, live_code, post, reset_all, svc, wait)


def set_route(service, pct):
    docker("exec", "aidevops-ops", "sh", "-c", f"echo '{{\"{service}\": {pct}}}' > /runtime/routes.json")
    time.sleep(0.5)


def routes_file():
    return docker("exec", "aidevops-ops", "cat", "/runtime/routes.json").stdout.strip()


def canary_file(name):
    return docker("exec", "aidevops-ops", "cat", f"/runtime/{name}-canary/main.py").stdout


def served_by(n=60, path="/catalog/products"):
    c = Counter()
    for _ in range(n):
        r = httpx.get(f"{GW}{path}", timeout=10)
        c[(r.headers.get("x-served-by"), r.status_code)] += 1
    return c


def settings(**params):
    return httpx.post(f"{OPS}/api/settings", params=params, timeout=10).json()


check(reset_all(), "stack healthy before testing")
check(docker("exec", "aidevops-ops", "ls", "/runtime/catalog-canary/main.py").returncode == 0, "canary twins exist")

# ---------------------------------------------------------------------------------------------
print("\n== 1. The gateway really splits traffic between stable and canary")
set_route("catalog", 0)
c = served_by(40)
check(set(h for h, _ in c) == {"catalog"}, "0%: every request is served by the stable instance", str(c))
set_route("catalog", 100)
c = served_by(40)
check(set(h for h, _ in c) == {"catalog-canary"}, "100%: every request is served by the canary", str(c))
set_route("catalog", 50)
c = served_by(80)
stable, canary_n = sum(v for (h, _), v in c.items() if h == "catalog"), sum(v for (h, _), v in c.items() if h == "catalog-canary")
check(stable > 10 and canary_n > 10 and all(code == 200 for _, code in c), f"50%: both instances serve traffic and every request succeeds ({stable} stable / {canary_n} canary)")

print("   stopping the canary container while 100% of traffic is routed to it")
set_route("catalog", 100)
docker("stop", "aidevops-catalog-canary")
time.sleep(1)
c = served_by(20)
check(set(h for h, _ in c) == {"catalog"} and all(code == 200 for _, code in c),
      "a down canary costs users nothing: requests fall back to the stable instance", str(c))
docker("start", "aidevops-catalog-canary")
docker("exec", "aidevops-ops", "sh", "-c", "echo '{}' > /runtime/routes.json")  # leave no hand-made routes behind
check(wait(lambda: httpx.get("http://localhost:9000/api/health", timeout=3).status_code == 200, 20), "control plane unaffected")
time.sleep(6)

# ---------------------------------------------------------------------------------------------
print("\n== 2. A good patch passes the canary and is promoted")
settings(canary_enabled="true", canary_percent=50, canary_observe=12)
check(break_service("cart", "cart-overwrite"), "cart broken (overwrites quantity)")
broken = live_code("cart")
good = golden_code("cart")
r = httpx.post(f"{OPS}/api/services/cart/propose", json={"code": good}, timeout=20)
check(r.status_code == 200 and svc("cart")["repair"]["state"] == "pending_approval", "good patch held for approval")
check(live_code("cart") == broken, "stable still runs the broken code while pending")
check(post("/api/services/cart/approve").status_code == 200, "approved")

seen, states = Counter(), []
end = time.time() + 70
while time.time() < end:
    st = svc("cart")["repair"]["state"]
    if not states or states[-1] != st:
        states.append(st)
    if st == "resolved":
        break
    if st == "canary":  # real shoppers using the cart while the canary is being tested
        for _ in range(4):
            r = httpx.get(f"{GW}/cart/cart/canary-shopper", timeout=10)
            seen[r.headers.get("x-served-by")] += 1
    time.sleep(0.6)
check(states[-2:] == ["canary", "resolved"], "the stages are Canary -> Resolved (Approved lasts only an instant)", str(states))
check(seen.get("cart-canary", 0) > 0 and seen.get("cart", 0) > 0,
      "during the canary, real requests were served by BOTH the canary and the stable instance", str(dict(seen)))
rp = svc("cart")["repair"]
ci = rp["canary"]
check(ci and ci["result"] == "passed" and ci["probes_ok"] >= 3 and ci["probes_failed"] == 0,
      "the canary report records passed health checks", str(ci))
check(ci["requests"] > 0, "and counts the real requests it served", str(ci))
check(live_code("cart") == good, "the patch was promoted to the stable service")
check(canary_file("cart") == good, "the canary twin now matches stable")
check(routes_file() == "{}", "the traffic split was closed afterwards", routes_file())
check(svc("cart")["status"] == "healthy" and svc("cart")["bug"] is None, "service healthy, fault cleared")
verifier = [a for a in rp["proposal"]["agents"] if a["agent"] == "Verifier"]
check(len(verifier) == 1 and "passed the health probe" in verifier[0]["summary"], "the Verifier step records the final check")

# ---------------------------------------------------------------------------------------------
print("\n== 3. A bad patch fails the canary and never reaches the stable service")
check(break_service("catalog", "catalog-price"), "catalog broken (price in the wrong unit)")
broken = live_code("catalog")
bad = broken + "\n# attempted fix that changes nothing\n"
httpx.post(f"{OPS}/api/services/catalog/propose", json={"code": bad}, timeout=20)
post("/api/services/catalog/approve")
check(wait(lambda: svc("catalog")["repair"]["state"] == "canary_failed", 60), "state becomes Canary failed")
rp = svc("catalog")["repair"]
check("did not become healthy" in rp["note"] or "health check" in rp["note"], "the reason is recorded", rp["note"])
check(live_code("catalog") == broken, "the stable service was NOT touched by the bad patch")
check(routes_file() == "{}", "no traffic was ever left on the failed canary")
check(canary_file("catalog") == golden_code("catalog"), "the canary was reset to the last known-good code")
# the badge shows "fixing" until the repair thread ends and the next health check runs, so wait for it to settle
check(rp["state"] != "resolved" and wait(lambda: svc("catalog")["status"] in ("broken", "down"), 20),
      "the incident is not marked resolved and the service is still (correctly) broken", svc("catalog")["status"])
check(post("/api/services/catalog/fix", mode="golden").status_code == 200, "operator restores known-good")
check(wait(lambda: svc("catalog")["status"] == "healthy", 40), "service recovered")

# ---------------------------------------------------------------------------------------------
print("\n== 4. With canary off the patch goes straight to the stable service (no canary stage)")
settings(canary_enabled="false")
check(break_service("cart", "cart-overwrite"), "cart broken again")
httpx.post(f"{OPS}/api/services/cart/propose", json={"code": golden_code("cart")}, timeout=20)
post("/api/services/cart/approve")
states = []
end = time.time() + 40
while time.time() < end:
    st = svc("cart")["repair"]["state"]
    if not states or states[-1] != st:
        states.append(st)
    if st == "resolved":
        break
    time.sleep(0.4)
check("canary" not in states and states[-1] == "resolved", "no canary stage when canary is off", str(states))

settings(canary_enabled="false")
reset_all()
finish()
