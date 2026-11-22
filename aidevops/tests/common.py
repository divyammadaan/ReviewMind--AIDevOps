"""Helpers shared by the end-to-end tests. They talk to the real running stack."""
import os
import subprocess
import sys
import time

import httpx

OPS, GW = "http://localhost:9000", "http://localhost:8080/api"
RESULTS = {"pass": 0, "fail": 0}
ENGINE = os.getenv("CONTAINER_CLI", "docker")  # set CONTAINER_CLI=podman to run the tests against Podman


def state():
    return {s["name"]: s for s in httpx.get(f"{OPS}/api/state", timeout=10).json()["services"]}


def svc(name):
    return state()[name]


def events():
    return httpx.get(f"{OPS}/api/state", timeout=10).json()["events"]


def wait(pred, timeout=60, every=1.5):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if pred():
                return True
        except Exception:
            pass
        time.sleep(every)
    return False


def check(cond, msg, detail=""):
    RESULTS["pass" if cond else "fail"] += 1
    print(("PASS " if cond else "FAIL ") + msg + (f"   [{detail}]" if detail and not cond else ""), flush=True)
    return cond


def post(path, **params):
    return httpx.post(f"{OPS}{path}", params=params or None, timeout=30)


def live_code(name):
    """The service's main.py as it is on the shared volume right now."""
    return subprocess.run([ENGINE, "exec", "aidevops-ops", "cat", f"/runtime/{name}/main.py"],
                          capture_output=True, text=True, encoding="utf-8", check=True).stdout


def golden_code(name):
    return subprocess.run([ENGINE, "exec", "aidevops-ops", "cat", f"/golden/{name}/main.py"],
                          capture_output=True, text=True, encoding="utf-8", check=True).stdout


def docker(*args):
    return subprocess.run([ENGINE, *args], capture_output=True, text=True, encoding="utf-8")


def break_service(name, bug_id):
    r = post(f"/api/services/{name}/break", bug_id=bug_id, report="false")
    r.raise_for_status()
    return wait(lambda: svc(name)["status"] in ("broken", "down"), 40)


def reset_all():
    post("/api/reset")
    time.sleep(9)  # a reset rewrites every file and restarts every service; the old "healthy" badges are stale
    return wait(lambda: all(s["status"] == "healthy" for s in state().values()), 80)


def finish():
    print(f"\n{RESULTS['pass']} passed, {RESULTS['fail']} failed")
    sys.exit(1 if RESULTS["fail"] else 0)


def has_discount_fix(text):
    """True if the code multiplies by a 10% rate however it is spelled (0.10, 0.1, .1 ...)."""
    import re
    return bool(re.search(r"[*]\s*0?[.]10?(?![0-9])", text))
