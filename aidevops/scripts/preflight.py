"""Pre-flight check: run this a few minutes before a demo.

    python scripts/preflight.py

It checks the real system (nothing is assumed): the container engine, every container, service health, the
Groq keys (one real call of a few tokens), the GitHub token (read-only), telemetry and the phone app. Exit code
0 means ready; any FAIL means fix it first. WARN means it will work but something is worth knowing.
"""
import os
import subprocess
import sys

import httpx

OPS, SHOP = "http://localhost:9000", "http://localhost:8080"
EXPECTED = ["gateway", "catalog", "inventory", "cart", "payments", "orders", "metrics", "ops"] + \
           [f"{s}-canary" for s in ("catalog", "inventory", "cart", "payments", "orders")]
results = []


def say(level, msg):
    results.append(level)
    print(f"{level:<5} {msg}")


def get(url, **kw):
    return httpx.get(url, timeout=kw.pop("timeout", 10), **kw)


# 1. container engine and containers ----------------------------------------------------------------------
try:
    out = subprocess.run([os.getenv("CONTAINER_CLI", "docker"), "ps", "--format", "{{.Names}}"], capture_output=True, text=True, timeout=20)
    running = set(out.stdout.split())
except (OSError, subprocess.TimeoutExpired):
    running = set()
if not running:
    say("FAIL", "no containers are running (is Docker Desktop started? then: docker compose up -d)")
else:
    missing = [n for n in EXPECTED if f"aidevops-{n}" not in running]
    say("PASS" if not missing else "FAIL", f"{len(EXPECTED) - len(missing)}/{len(EXPECTED)} containers running" +
        (f"; missing: {', '.join(missing)}" if missing else ""))

# 2. control plane and service health ------------------------------------------------------------------------
try:
    st = get(f"{OPS}/api/state").json()
except Exception as e:
    say("FAIL", f"control plane not reachable on :9000 ({type(e).__name__})")
    sys.exit(1)
bad = {s["name"]: s["status"] for s in st["services"] if s["status"] != "healthy"}
say("PASS" if not bad else "FAIL", "all 6 services healthy" if not bad else f"services not healthy: {bad} (try 'Reset all')")
pend = [s["name"] for s in st["services"] if s["repair"]["state"] not in ("idle",)]
say("PASS" if not pend else "WARN", "no leftover incidents from earlier" if not pend else f"leftover incident state on: {', '.join(pend)} (Reset all clears it)")

# 3. storefront ----------------------------------------------------------------------------------------------
try:
    products = get(f"{SHOP}/api/catalog/products").json()
    say("PASS" if len(products) == 6 else "FAIL", f"storefront serves {len(products)} products")
except Exception as e:
    say("FAIL", f"storefront not reachable on :8080 ({type(e).__name__})")

# 4. Groq keys: a real call --------------------------------------------------------------------------------------
llm = get(f"{OPS}/api/llm/check", timeout=40).json()
nkeys = len(st["llm"]["keys"])
if llm["ok"]:
    say("PASS", f"Groq works now ({nkeys} key(s) configured; answered via the {llm['key']} key, {llm['model']})")
    if nkeys < 2:
        say("WARN", "only one Groq key: no fallback if it hits its daily limit")
else:
    say("FAIL", f"Groq call failed: {llm['error']}")

# 5. GitHub ----------------------------------------------------------------------------------------------------------
gh = get(f"{OPS}/api/github/check", timeout=30).json()
if gh["ok"]:
    say("PASS", f"GitHub reachable: {gh['repo']} (reporting {'ON' if gh['enabled'] else 'OFF'})")
    if not gh["enabled"]:
        say("WARN", "GitHub reporting is off: no incident pull requests will be opened (click the GitHub chip to enable)")
else:
    say("WARN", f"GitHub not usable ({gh['error']}): the demo works but without incident pull requests")

# 6. telemetry ---------------------------------------------------------------------------------------------------------
t = get(f"{OPS}/api/telemetry").json()
say("PASS" if t["spans_received"] and t["metrics_received"] and t["logs_received"] else "FAIL",
    f"telemetry flowing: {t['spans_received']} spans, {t['metrics_received']} metric points, {t['logs_received']} logs")

# 7. phone app ---------------------------------------------------------------------------------------------------------
say("PASS" if get(f"{OPS}/m/").status_code == 200 else "FAIL", "phone app is served at /m/")
mob = st["mobile"]
say("PASS" if mob["subscribers"] else "WARN",
    f"{mob['subscribers']} phone(s) subscribed to alerts" if mob["subscribers"] else "no phone is subscribed yet (open the phone app and tap Enable alerts)")

# 8. settings ----------------------------------------------------------------------------------------------------------
S = st["settings"]
print(f"INFO  settings: watchdog {'on' if S['watchdog'] else 'off'}, agents {'on' if S['agentic'] else 'off'}, "
      f"canary {'on (%d%%)' % S['canary']['percent'] if S['canary']['enabled'] else 'off'}")

fails, warns = results.count("FAIL"), results.count("WARN")
print(f"\n{'READY' if not fails else 'NOT READY'}: {results.count('PASS')} passed, {warns} warning(s), {fails} failure(s)")
sys.exit(1 if fails else 0)
