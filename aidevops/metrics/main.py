"""Metrics sidecar: per-container CPU and memory, read from the Docker stats API.

This is the only container with the Docker socket mounted, and it exposes exactly one
read-only endpoint, /stats. The control plane never touches Docker itself.
"""
import threading
import time

import httpx
from fastapi import FastAPI

NAMES = ["gateway", "catalog", "inventory", "cart", "payments", "orders", "ops"]
SOCK = "/var/run/docker.sock"

app = FastAPI(title="metrics")
latest: dict[str, dict] = {}


def read(stats: dict) -> dict:
    cpu, mem = stats["cpu_stats"], stats["memory_stats"]
    pre = stats.get("precpu_stats", {})
    cpu_delta = cpu["cpu_usage"]["total_usage"] - pre.get("cpu_usage", {}).get("total_usage", 0)
    sys_delta = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
    cores = cpu.get("online_cpus") or len(cpu["cpu_usage"].get("percpu_usage") or []) or 1
    pct = cpu_delta / sys_delta * cores * 100 if sys_delta > 0 and cpu_delta >= 0 else 0.0
    sub = mem.get("stats", {})
    used = mem.get("usage", 0) - sub.get("inactive_file", sub.get("total_inactive_file", 0))
    return {"cpu": round(pct, 1), "mem_mb": round(used / 1048576, 1)}


def sample(name: str):
    client = httpx.Client(transport=httpx.HTTPTransport(uds=SOCK), base_url="http://docker", timeout=8)
    while True:
        try:
            r = client.get(f"/containers/aidevops-{name}/stats", params={"stream": "false"})
            latest[name] = read(r.json()) | {"t": time.time()}
        except Exception:  # container stopped, daemon busy: drop the reading and retry
            latest.pop(name, None)
            time.sleep(2)


@app.on_event("startup")
def start():
    for n in NAMES:
        threading.Thread(target=sample, args=(n,), daemon=True).start()


@app.get("/stats")
def stats():
    return {"containers": latest}
