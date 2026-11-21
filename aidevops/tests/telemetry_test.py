"""End-to-end tests of the OpenTelemetry integration against the real running stack.

    python tests/telemetry_test.py

Nothing is mocked: spans are produced by the real services, exported over OTLP/HTTP to the control plane,
and read back through its API. The last section stops the collector (the control plane) to prove that the
application does not depend on it.
"""
import time

import httpx

from common import GW, OPS, check, break_service, docker, finish, post, reset_all, svc, wait


def find_trace(kind, needle, timeout=20):
    """Newest trace of `kind` whose root label contains `needle`."""
    out = {}

    def poll():
        for t in httpx.get(f"{OPS}/api/traces", params={"kind": kind, "limit": 60}, timeout=10).json()["traces"]:
            if needle in t["root"]:
                out["t"] = t
                return True
        return False

    return out.get("t") if wait(poll, timeout, every=1) else None


def detail(trace_id):
    return httpx.get(f"{OPS}/api/traces/{trace_id}", timeout=10).json()


check(reset_all(), "stack healthy before testing")

# ---------------------------------------------------------------------------------------------
print("\n== 1. One trace ID follows a checkout across every microservice")
user = "otel-test-1"
httpx.post(f"{GW}/cart/cart/{user}/add", json={"product_id": 1, "qty": 2}, timeout=10)
r = httpx.post(f"{GW}/orders/checkout/{user}", timeout=10)
check(r.status_code == 200, "checkout through the gateway succeeds", str(r.status_code))
t = find_trace("user", f"/api/orders/checkout/{user}")
check(t is not None, "the checkout appears as a storefront-origin trace")
d = detail(t["trace_id"])
spans = d["spans_detail"]
services = {s["service"] for s in spans}
check({"gateway", "orders", "cart", "catalog", "inventory", "payments"} <= services,
      "ONE trace contains spans from all six services", str(sorted(services)))
ids = {s["id"] for s in spans}
roots = [s for s in spans if s["parent"] not in ids]
check(len(roots) == 1 and roots[0]["service"] == "gateway", "exactly one root span, created by the gateway",
      str([(s["service"], s["name"]) for s in roots]))
by_id = {s["id"]: s for s in spans}
cart_server = next(s for s in spans if s["service"] == "cart" and s["kind"] == "server" and s["name"].startswith("GET /cart/"))
parent = by_id[cart_server["parent"]]
check(parent["service"] == "orders" and parent["kind"] == "client",
      "cart's server span is a child of orders' outgoing HTTP call (context propagated in headers)",
      f"{parent['service']} {parent['kind']}")
catalog_server = next(s for s in spans if s["service"] == "catalog" and s["kind"] == "server")
check(by_id[by_id[catalog_server["parent"]]["parent"]]["service"] == "cart",
      "the chain continues: gateway -> orders -> cart -> catalog")
check(all(s["dur_ms"] > 0 for s in spans) and all(s["http_status"] == 200 for s in spans if s["kind"] == "server"),
      "every server span carries a duration and its HTTP status code (200)")
check(d["error"] is False and d["origin"] == "user", "trace is marked successful and storefront-origin")

# ---------------------------------------------------------------------------------------------
print("\n== 2. A failing request is traced to the service where it actually broke")
check(break_service("orders", "orders-port"), "orders broken (calls payments on the wrong port)")
user = "otel-test-2"
httpx.post(f"{GW}/cart/cart/{user}/add", json={"product_id": 1, "qty": 2}, timeout=10)
r = httpx.post(f"{GW}/orders/checkout/{user}", timeout=15)
check(r.status_code >= 500, "the storefront request fails", str(r.status_code))
t = find_trace("errors", f"/api/orders/checkout/{user}")
check(t is not None and t["error"], "the failing request appears in the Failing traces list")
f = t["failing"]
check(f["service"] == "orders" and "ConnectError" in f["message"] and "9140" in (f.get("calling") or ""),
      "failure localised: orders -> payments:9140 ConnectError", str(f))
d = detail(t["trace_id"])
gw = next(s for s in d["spans_detail"] if s["service"] == "gateway" and s["kind"] == "server")
check(gw["http_status"] >= 500 and gw["error"], "the gateway span shows the 5xx the user saw", str(gw["http_status"]))
check("payments" not in {s["service"] for s in d["spans_detail"]}, "no payments span exists: the call never reached it")
hint = httpx.get(f"{OPS}/api/state", timeout=10).json()
check(any(s["name"] == "orders" and s["tele"] and s["tele"]["error_pct"] > 0 for s in hint["services"]),
      "per-service stats on the dashboard show orders' error rate")
post("/api/services/orders/fix", mode="golden")
wait(lambda: svc("orders")["status"] == "healthy", 40)

# ---------------------------------------------------------------------------------------------
print("\n== 3. Health probes are traced too, and labelled so they do not pollute storefront traffic")
probes = httpx.get(f"{OPS}/api/traces", params={"kind": "probe", "limit": 60}, timeout=10).json()["traces"]
check(len(probes) > 0 and all(p["origin"] == "probe" for p in probes), "probe traces exist and are labelled origin=probe")
check(any({"ops", "orders", "cart"} <= set(p["services"]) for p in probes),
      "the probe label propagates across services (ops -> orders -> cart share one trace)")
user_traces = httpx.get(f"{OPS}/api/traces", params={"kind": "user", "limit": 100}, timeout=10).json()["traces"]
check(all(u["origin"] == "user" for u in user_traces), "the storefront list contains no probe traffic")

# ---------------------------------------------------------------------------------------------
print("\n== 4. Telemetry is not a hard dependency: stop the collector and the app keeps working")
check(docker("stop", "aidevops-ops").returncode == 0, "collector (control plane) stopped")
t0 = time.time()
r = httpx.get(f"{GW}/catalog/products", timeout=10)
check(r.status_code == 200 and len(r.json()) == 6 and time.time() - t0 < 3, "storefront API still answers quickly", f"{time.time() - t0:.2f}s")
httpx.post(f"{GW}/cart/cart/otel-down/add", json={"product_id": 2, "qty": 1}, timeout=10)
r = httpx.post(f"{GW}/orders/checkout/otel-down", timeout=15)
check(r.status_code == 200 and r.json()["status"] == "confirmed", "a full checkout still succeeds with no collector")

print("   restarting two services while the collector is down (start-up must not depend on it)")
docker("restart", "aidevops-catalog", "aidevops-gateway")


def serving():
    try:
        return httpx.get(f"{GW}/catalog/products", timeout=3).status_code == 200
    except Exception:
        return False


check(wait(serving, 40, every=2), "services start and serve with the collector absent")
check(docker("start", "aidevops-ops").returncode == 0, "collector started again")
check(wait(lambda: httpx.get(f"{OPS}/api/health", timeout=3).status_code == 200, 40), "control plane back")
n0 = httpx.get(f"{OPS}/api/telemetry", timeout=5).json()["spans_received"]
time.sleep(3)
httpx.post(f"{GW}/cart/cart/otel-up/add", json={"product_id": 3, "qty": 1}, timeout=10)
httpx.post(f"{GW}/orders/checkout/otel-up", timeout=15)
check(wait(lambda: httpx.get(f"{OPS}/api/telemetry", timeout=5).json()["spans_received"] > n0 + 5, 30),
      "spans flow again automatically once the collector returns")
check(wait(lambda: all(s["status"] == "healthy" for s in httpx.get(f"{OPS}/api/state", timeout=5).json()["services"]), 60),
      "everything healthy afterwards")

reset_all()
finish()
