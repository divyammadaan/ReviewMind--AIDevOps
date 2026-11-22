"""Set up the "safety net" demo: a service breaks, and a plausible-looking but WRONG patch is proposed.

    python scripts/demo_bad_patch.py [service]        # default: cart

It injects a fault, then submits a patch that compiles but does not fix it (as a manual patch, so no AI tokens
are used). The patch is only *proposed*. Approve it on the dashboard or the phone and watch what happens:
  - with Canary ON the canary refuses it and the running service is never touched ("Canary failed")
  - with Canary OFF it is deployed, fails verification and is rolled back automatically
Either way the incident is never marked resolved. No GitHub pull request is opened by this script.
"""
import os
import subprocess
import sys
import time

import httpx

OPS = "http://localhost:9000"
FAULT = {"cart": "cart-discount", "catalog": "catalog-price", "inventory": "inventory-decrement",
         "payments": "payments-zero", "orders": "orders-predisc"}

svc = sys.argv[1] if len(sys.argv) > 1 else "cart"
if svc not in FAULT:
    sys.exit(f"unknown service {svc!r}; choose one of: {', '.join(FAULT)}")

# The watchdog would start its own AI proposal the moment the service fails and race this script: pause it.
watchdog_was_on = httpx.get(f"{OPS}/api/state", timeout=10).json()["settings"]["watchdog"]
if watchdog_was_on:
    httpx.post(f"{OPS}/api/settings", params={"watchdog": "false"}, timeout=10)
r = httpx.post(f"{OPS}/api/services/{svc}/break", params={"bug_id": FAULT[svc], "report": "false"}, timeout=15)
r.raise_for_status()
print(f"Injected: {r.json()['title']} in {svc}. Waiting for the health check to notice...")
for _ in range(30):
    state = {s["name"]: s for s in httpx.get(f"{OPS}/api/state", timeout=10).json()["services"]}[svc]
    if state["status"] in ("broken", "down"):
        break
    time.sleep(1.5)

code = subprocess.run([os.getenv("CONTAINER_CLI", "docker"), "exec", "aidevops-ops", "cat", f"/runtime/{svc}/main.py"], capture_output=True, text=True,
                      encoding="utf-8").stdout
bad = code + "\n# attempted fix: restart the calculation (changes nothing)\n"
r = httpx.post(f"{OPS}/api/services/{svc}/propose", json={"code": bad}, timeout=15)
r.raise_for_status()
if watchdog_was_on:  # safe to restore: a pending proposal means the watchdog has nothing to do
    httpx.post(f"{OPS}/api/settings", params={"watchdog": "true"}, timeout=10)
print(f"A wrong patch for {svc} is now waiting for approval. Approve it and watch the safety net.")
