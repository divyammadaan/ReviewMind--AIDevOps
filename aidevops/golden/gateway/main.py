"""API gateway: serves the storefront and forwards /api/<service>/... calls."""
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="gateway")

SERVICES = {"catalog": 9101, "inventory": 9102, "cart": 9103, "payments": 9104, "orders": 9105}


@app.get("/health")
def health():
    return {"ok": True}


@app.api_route("/api/{svc}/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy(svc: str, path: str, request: Request):
    port = SERVICES.get(svc)
    if port is None:
        return JSONResponse({"error": "unknown service", "service": svc}, status_code=404)
    try:
        async with httpx.AsyncClient(timeout=4) as client:
            r = await client.request(
                request.method, f"http://{svc}:{port}/{path}",
                params=request.query_params, content=await request.body(),
                headers={"content-type": request.headers.get("content-type", "application/json")})
    except httpx.HTTPError:
        return JSONResponse({"error": f"{svc} service unavailable", "service": svc}, status_code=502)
    return Response(r.content, status_code=r.status_code, media_type=r.headers.get("content-type"))


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
