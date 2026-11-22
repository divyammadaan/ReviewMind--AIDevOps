"""Canary routing: tell the gateway what share of a service's traffic goes to its canary copy.

The gateway re-reads /runtime/routes.json whenever it changes, so opening or closing a traffic split is a
single atomic file write. Nothing else is needed: the canary is an ordinary container running the same image.
"""
import json
import os
from pathlib import Path

import probes

ROUTES = Path("/runtime/routes.json")
PORTS = {"catalog": 9101, "inventory": 9102, "cart": 9103, "payments": 9104, "orders": 9105}


def _read():
    try:
        return json.loads(ROUTES.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        return {}


def _write(data):
    tmp = ROUTES.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, ROUTES)  # atomic: the gateway never sees a half-written file


def set_route(svc: str, percent: float):
    data = _read()
    if percent > 0:
        data[svc] = percent
    else:
        data.pop(svc, None)
    _write(data)


def clear_routes():
    _write({})


def urls_for(svc: str) -> dict:
    """The probe target map with this service pointed at its canary copy; everything else stays stable."""
    urls = dict(probes.URL)
    urls[svc] = f"http://{svc}-canary:{PORTS[svc]}"
    return urls
