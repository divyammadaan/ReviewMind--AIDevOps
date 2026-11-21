"""End-to-end tests of OpenTelemetry metrics and logs against the real running stack.

    python tests/observability_test.py

Nothing is mocked: traffic goes through the real gateway, services export OTLP metrics and logs to the
control plane, and the numbers are read back through its API. Counts are compared as before/after
differences, because the control plane's own health probes also add (a little) traffic.
"""
import time

import httpx

from common import GW, OPS, check, break_service, docker, finish, post, reset_all, svc, wait


def metrics():
    return httpx.get(f"{OPS}/api/metrics", timeout=10).json()["services"]


def total(s):
    return metrics().get(s, {}).get("total", 0)


def logs(**params):
    return httpx.get(f"{OPS}/api/logs", params=params, timeout=10).json()["logs"]


check(reset_all(), "stack healthy before testing")
time.sleep(12)  # let every service export a first batch

# ---------------------------------------------------------------------------------------------
print("\n== 1. Metrics reflect real traffic")
before = {s: total(s) for s in ("gateway", "catalog")}
check(all(v > 0 for v in before.values()), "gateway and catalog are exporting metrics", str(before))
N = 40
for _ in range(N):
    httpx.get(f"{GW}/catalog/products", timeout=10)
ok = wait(lambda: total("gateway") - before["gateway"] >= N and total("catalog") - before["catalog"] >= N, 30, every=2)
check(ok, f"after {N} requests, gateway and catalog request counts each rose by at least {N}",
      f"gateway +{total('gateway') - before['gateway']}, catalog +{total('catalog') - before['catalog']}")
m = metrics()
check(m["gateway"]["rate"] > 0 and m["gateway"]["p95_ms"] and m["gateway"]["p95_ms"] > 0,
      "request rate and p95 latency are computed from the histograms", str(m["gateway"]))
check(m["catalog"]["p50_ms"] <= m["catalog"]["p95_ms"] <= m["catalog"]["p99_ms"], "percentiles are ordered p50 <= p95 <= p99")
check(m["gateway"]["error_pct"] == 0 and m["catalog"]["error_pct"] == 0, "no errors reported while everything is healthy")

# ---------------------------------------------------------------------------------------------
print("\n== 2. A failing service shows up as an error rate and as error logs with a stack trace")
user = "obs-test"
check(break_service("cart", "cart-keyerror"), "cart broken (reads a field that does not exist)")
# add AFTER the fault: injecting it restarts the service, which empties its in-memory carts
httpx.post(f"{GW}/cart/cart/{user}/add", json={"product_id": 1, "qty": 1}, timeout=10)
statuses = [httpx.get(f"{GW}/cart/cart/{user}", timeout=10).status_code for _ in range(8)]
check(all(s >= 500 for s in statuses), "every request for that cart fails with a 5xx", str(statuses))
check(wait(lambda: metrics().get("cart", {}).get("error_pct", 0) > 0, 30, every=2), "cart's error rate rises above 0 in the metrics",
      str(metrics().get("cart")))
check(wait(lambda: metrics().get("gateway", {}).get("error_pct", 0) > 0, 30, every=2), "the gateway's error rate rises too (it relayed the 5xx)")
check(wait(lambda: any(r["service"] == "cart" and r.get("exception") for r in logs(level="warn", service="cart")), 30, every=2),
      "an ERROR log from cart arrives with an exception type")
err = next(r for r in logs(level="warn", service="cart") if r.get("stacktrace"))
check("KeyError" in err["stacktrace"] and "cost" in err["stacktrace"], "the log carries the real stack trace (KeyError: 'cost')",
      (err["stacktrace"] or "")[-160:])
check(err["level"] in ("ERROR", "FATAL"), "its severity is ERROR", err["level"])

# ---------------------------------------------------------------------------------------------
print("\n== 3. Logs are linked to traces by trace ID")
tr = None
if wait(lambda: any(f"/api/cart/cart/{user}" in t["root"] for t in httpx.get(f"{OPS}/api/traces", params={"kind": "errors", "limit": 60}, timeout=10).json()["traces"]), 30, every=2):
    tr = next(t for t in httpx.get(f"{OPS}/api/traces", params={"kind": "errors", "limit": 60}, timeout=10).json()["traces"]
              if f"/api/cart/cart/{user}" in t["root"])
check(tr is not None, "the failing request has a trace")
linked = httpx.get(f"{OPS}/api/traces/{tr['trace_id']}/logs", timeout=10).json()
by_trace = linked["by_trace"]
check(len(by_trace) > 0 and all(r["trace_id"] == tr["trace_id"] for r in by_trace), f"{len(by_trace)} log lines are stamped with exactly this trace ID")
check({"gateway", "cart"} <= {r["service"] for r in by_trace}, "the linked logs come from more than one service (gateway and cart)",
      str(sorted({r["service"] for r in by_trace})))
check(any("500" in r["message"] for r in by_trace), "the linked logs include the 500 response line")
errs_in_trace = [r for r in logs(level="warn", service="cart") if r.get("stacktrace") and r["trace_id"] == tr["trace_id"]]
print(f"   info: stack-trace log carries the trace ID: {bool(errs_in_trace)}; "
      f"if not, the dashboard shows it under 'nearby logs (matched by time only)': {len(linked['by_time'])} found", flush=True)
post("/api/services/cart/fix", mode="golden")
wait(lambda: svc("cart")["status"] == "healthy", 40)

# ---------------------------------------------------------------------------------------------
print("\n== 4. The collector does not log about itself (no feedback loop)")
n1 = len(logs(level="all", service="ops", limit=300))
time.sleep(20)
n2 = len(logs(level="all", service="ops", limit=300))
check(n2 - n1 <= 3, "the control plane produced almost no log lines of its own in 20 s", f"{n1} -> {n2}")
check(n2 < 60, "total self-generated logs stay small", str(n2))

# ---------------------------------------------------------------------------------------------
print("\n== 5. Metrics and logs are not hard dependencies either")
check(docker("stop", "aidevops-ops").returncode == 0, "collector (control plane) stopped")
t0 = time.time()
r = httpx.get(f"{GW}/catalog/products", timeout=10)
check(r.status_code == 200 and time.time() - t0 < 3, "storefront API still answers quickly", f"{time.time() - t0:.2f}s")
httpx.post(f"{GW}/cart/cart/obs-down/add", json={"product_id": 2, "qty": 1}, timeout=10)
r = httpx.post(f"{GW}/orders/checkout/obs-down", timeout=15)
check(r.status_code == 200 and r.json()["status"] == "confirmed", "a full checkout still succeeds with no collector")
docker("restart", "aidevops-catalog", "aidevops-gateway")


def serving():
    try:
        return httpx.get(f"{GW}/catalog/products", timeout=3).status_code == 200
    except Exception:
        return False


check(wait(serving, 40, every=2), "services restart and serve with the collector absent")
check(docker("start", "aidevops-ops").returncode == 0, "collector started again")
check(wait(lambda: httpx.get(f"{OPS}/api/health", timeout=3).status_code == 200, 40), "control plane back")
time.sleep(5)
a = httpx.get(f"{OPS}/api/telemetry", timeout=5).json()
for _ in range(5):
    httpx.get(f"{GW}/catalog/products", timeout=10)
check(wait(lambda: httpx.get(f"{OPS}/api/telemetry", timeout=5).json()["logs_received"] > a["logs_received"]
           and httpx.get(f"{OPS}/api/telemetry", timeout=5).json()["metrics_received"] > a["metrics_received"], 40, every=2),
      "metrics and logs flow again automatically once the collector returns")

reset_all()
finish()
