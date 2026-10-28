"""End-to-end test: inject every fault, let the AI repair it, confirm the service recovers.

Needs the stack running (docker compose up) and a Groq key in ../.env.
    python tests/repair_all.py [--mode ai|golden] [--only cart,orders]
"""
import argparse
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))
from bugs import BUGS  # noqa: E402

OPS = "http://localhost:9000"


def state():
    return {s["name"]: s for s in httpx.get(f"{OPS}/api/state", timeout=10).json()["services"]}


def wait(pred, timeout):
    end = time.time() + timeout
    while time.time() < end:
        s = state()
        if pred(s):
            return s
        time.sleep(1.5)
    return state()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="ai")
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    only = set(filter(None, args.only.split(",")))

    s = wait(lambda s: all(v["status"] == "healthy" for v in s.values()), 60)
    assert all(v["status"] == "healthy" for v in s.values()), {k: v["status"] for k, v in s.items()}

    rows = []
    for svc, bugs in BUGS.items():
        if only and svc not in only:
            continue
        for b in bugs:
            httpx.post(f"{OPS}/api/services/{svc}/break", params={"bug_id": b["id"], "report": "false"}, timeout=10).raise_for_status()
            s = wait(lambda s: s[svc]["status"] in ("broken", "down"), 30)
            detected = s[svc]["status"] in ("broken", "down")
            t0 = time.time()
            httpx.post(f"{OPS}/api/services/{svc}/fix", params={"mode": args.mode}, timeout=10)
            time.sleep(2)
            s = wait(lambda s: s[svc]["status"] == "healthy" and not s[svc]["fixing"], 70)
            fixed = s[svc]["status"] == "healthy"
            lf = s[svc]["last_fix"] or {}
            rows.append((b["id"], detected, fixed, round(time.time() - t0, 1), lf.get("tokens", 0), lf.get("how", "-")))
            print(f'{b["id"]:22} detected={detected!s:5} fixed={fixed!s:5} {rows[-1][3]:>5}s '
                  f'{rows[-1][4]:>5} tok  {rows[-1][5]}', flush=True)
            # Isolate cases: always return to the original code, so one case's patch
            # (good or bad) cannot influence the next.
            httpx.post(f"{OPS}/api/services/{svc}/fix", params={"mode": "golden"}, timeout=10)
            wait(lambda s: s[svc]["status"] == "healthy" and not s[svc]["fixing"] and not s[svc]["bug"], 40)
            time.sleep(1)

    ok = sum(1 for r in rows if r[1] and r[2])
    print(f"\n{ok}/{len(rows)} faults detected and repaired; "
          f"{sum(r[4] for r in rows)} tokens total")
    sys.exit(0 if ok == len(rows) else 1)


if __name__ == "__main__":
    main()
