"""Tests of the simulated-traffic toggle against the real running stack.

    python tests/traffic_test.py

It switches the simulator on and measures what it really does for about a minute: that it starts and stops on
the toggle, that the rate varies but never exceeds the cap, that the shop stays healthy (no false alarms), that
the traffic shows up in the traces, and that the stock the health probes depend on is never touched.
"""
import time

import httpx

from common import GW, OPS, check, finish, post, reset_all, state, wait


def traffic():
    return httpx.get(f"{OPS}/api/state", timeout=10).json()["settings"]["traffic"]


def user_traces():
    return httpx.get(f"{OPS}/api/traces", params={"kind": "user", "limit": 200}, timeout=10).json()["traces"]


check(reset_all(), "stack healthy before testing")
post("/api/settings", traffic_enabled="false")
time.sleep(2)

print("\n== 1. Off by default and when switched off: no traffic")
a = traffic()
time.sleep(5)
b = traffic()
check(not a["enabled"] and a["rps"] == 0, "the toggle is off and the rate is 0")
check(b["sent"] == a["sent"], "no requests are sent while it is off", f"{a['sent']} -> {b['sent']}")

print("\n== 2. Switching on starts traffic")
stock_before = httpx.get(f"{GW}/inventory/stock/1", timeout=10).json()["in_stock"]
traces_before = len(user_traces())
sent0 = traffic()["sent"]
post("/api/settings", traffic_enabled="true")
check(wait(lambda: traffic()["sent"] > sent0 + 3, 15), "requests start flowing within seconds")
t = traffic()
check(t["enabled"] and 0.3 <= t["rps"] <= 20.0, "the reported rate is inside the allowed range (up to 20 per second)", str(t))

print("\n== 3. Measure for 45 seconds: variable, capped, harmless")
rates, healthy_all = [], True
start_sent, start_time = traffic()["sent"], time.time()
while time.time() - start_time < 45:
    time.sleep(2.5)
    rates.append(traffic()["rps"])
    if not all(s["status"] == "healthy" for s in state().values()):
        healthy_all = False
elapsed = time.time() - start_time
sent = traffic()["sent"] - start_sent
per_sec = sent / elapsed
print(f"   rates seen: {rates}; measured {per_sec:.2f} requests/s over {elapsed:.0f}s")
check(max(rates) - min(rates) >= 3, "the rate varies over time (it is not a flat stream)", f"min {min(rates)} max {max(rates)}")
check(max(rates) <= 20.0 and min(rates) >= 0.3, "the rate always stays within the allowed range")
check(per_sec <= 22, "the measured throughput never exceeds the cap (plus a small margin)", f"{per_sec:.2f}/s")
check(per_sec >= 2, "and it is really sending traffic", f"{per_sec:.2f}/s")
check(healthy_all, "every service stayed healthy the whole time (no false alarms from the load)")
t = traffic()
check(t["errors"] <= max(2, 0.02 * t["sent"]), "almost no requests failed on a healthy shop", f"{t['errors']} of {t['sent']}")

print("\n== 4. The traffic is visible to the monitoring and does not disturb the probes")
check(len(user_traces()) > traces_before, "storefront traces appeared in the traces panel")
check(wait(lambda: any(e["msg"].startswith("Simulated traffic started") for e in
                       httpx.get(f"{OPS}/api/state", timeout=10).json()["events"]), 5), "the activity log records the start")
stock_after = httpx.get(f"{GW}/inventory/stock/1", timeout=10).json()["in_stock"]
check(stock_after == stock_before, "product 1 stock (what the probes measure) was never touched", f"{stock_before} -> {stock_after}")
others = [httpx.get(f"{GW}/inventory/stock/{p}", timeout=10).json()["in_stock"] for p in (2, 3, 4, 5, 6)]
check(all(o > 0 for o in others), "the other products never sold out (the warehouse top-up works)", str(others))

print("\n== 5. Switching off stops it")
post("/api/settings", traffic_enabled="false")
time.sleep(4)
a = traffic()
time.sleep(5)
b = traffic()
check(not a["enabled"] and a["rps"] == 0, "the rate drops to 0")
check(b["sent"] == a["sent"], "no more requests are sent", f"{a['sent']} -> {b['sent']}")
check(all(s["status"] == "healthy" for s in state().values()), "all services still healthy afterwards")

reset_all()
finish()
