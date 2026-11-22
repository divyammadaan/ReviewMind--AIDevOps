"""API gateway: serves the storefront and forwards /api/<service>/... calls.

Canary releases: while the control plane tries a patch on a service's canary copy, it writes the share of
traffic (a percentage) to /runtime/routes.json. That share of requests goes to <service>-canary instead of
the stable instance, and the X-Served-By response header says which instance answered. If the canary cannot be
reached, the request is retried on the stable instance, so a down canary never costs a user a request.
"""
import json
import random
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="gateway")

SERVICES = {"catalog": 9101, "inventory": 9102, "cart": 9103, "payments": 9104, "orders": 9105}
ROUTES = Path("/runtime/routes.json")
_cache = {"mtime": None, "data": {}}


def canary_percent(svc: str) -> float:
    """Share of traffic (0-100) the control plane wants on this service's canary; re-read when the file changes."""
    try:
        mtime = ROUTES.stat().st_mtime_ns
        if mtime != _cache["mtime"]:
            _cache["data"] = json.loads(ROUTES.read_text(encoding="utf-8") or "{}")
            _cache["mtime"] = mtime
        return float(_cache["data"].get(svc, 0))
    except (OSError, ValueError, TypeError):
        return 0.0


@app.get("/health")
def health():
    return {"ok": True}


async def _send(client, request, host, port, path, body):
    return await client.request(
        request.method, f"http://{host}:{port}/{path}",
        params=request.query_params, content=body,
        headers={"content-type": request.headers.get("content-type", "application/json")})


@app.api_route("/api/{svc}/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy(svc: str, path: str, request: Request):
    port = SERVICES.get(svc)
    if port is None:
        return JSONResponse({"error": "unknown service", "service": svc}, status_code=404)
    body = await request.body()
    host = f"{svc}-canary" if random.random() * 100 < canary_percent(svc) else svc
    async with httpx.AsyncClient(timeout=4) as client:
        try:
            r = await _send(client, request, host, port, path, body)
        except httpx.HTTPError:
            if host == svc:
                return JSONResponse({"error": f"{svc} service unavailable", "service": svc}, status_code=502)
            host = svc  # the canary is unreachable: serve this request from the stable instance
            try:
                r = await _send(client, request, host, port, path, body)
            except httpx.HTTPError:
                return JSONResponse({"error": f"{svc} service unavailable", "service": svc}, status_code=502)
    return Response(r.content, status_code=r.status_code, media_type=r.headers.get("content-type"),
                    headers={"x-served-by": host})


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
