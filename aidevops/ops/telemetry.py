"""OTLP/HTTP trace receiver and in-memory trace store for the control plane.

Every service (and the control plane itself) exports its spans here. Spans that share a trace ID are
stitched back into one distributed trace, so a request's path across gateway -> orders -> cart -> catalog
(and where it failed) can be inspected from the dashboard.
"""
import gzip
import threading
import time
from collections import deque
from urllib.parse import urlparse

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

CAPS = {"errors": 200, "user": 300, "probe": 120}  # traces kept per bucket
WINDOW = 300  # seconds of server spans used for per-service stats
KINDS = {2: "server", 3: "client"}


def _val(v):
    which = v.WhichOneof("value")
    return getattr(v, which) if which else None


def _attrs(kvs):
    return {kv.key: _val(kv.value) for kv in kvs}


class TraceStore:
    def __init__(self):
        self.lock = threading.Lock()
        self.traces: dict[str, dict] = {}
        self.reqs: dict[str, deque] = {}  # service -> (end_ts, duration_ms, is_error), user traffic only
        self.received = 0
        self.last_received = None

    # ------------------------------------------------------------------ ingest
    def ingest(self, body: bytes, encoding: str = "") -> int:
        if encoding == "gzip":
            body = gzip.decompress(body)
        req = ExportTraceServiceRequest()
        req.ParseFromString(body)
        n = 0
        with self.lock:
            for rs in req.resource_spans:
                service = _attrs(rs.resource.attributes).get("service.name", "unknown")
                for ss in rs.scope_spans:
                    for sp in ss.spans:
                        self._add(service, sp)
                        n += 1
            self.received += n
            self.last_received = time.time()
            self._prune()
        return n

    def _add(self, service, sp):
        a = _attrs(sp.attributes)
        status = a.get("http.response.status_code", a.get("http.status_code"))
        exc = next(({"type": _attrs(e.attributes).get("exception.type", ""),
                     "message": _attrs(e.attributes).get("exception.message", "")}
                    for e in sp.events if e.name == "exception"), None)
        # A 4xx is a service correctly refusing a bad request (the health probes send deliberately bad
        # ones). OpenTelemetry marks outgoing 4xx calls as errors, but they are not failures of the callee.
        is4xx = isinstance(status, int) and 400 <= status < 500
        error = (isinstance(status, int) and status >= 500) or (sp.status.code == 2 and not is4xx)
        span = {
            "id": sp.span_id.hex(), "parent": sp.parent_span_id.hex() or None, "name": sp.name,
            "service": service, "kind": KINDS.get(sp.kind, "internal"),
            "start": sp.start_time_unix_nano / 1e9, "dur_ms": round((sp.end_time_unix_nano - sp.start_time_unix_nano) / 1e6, 2),
            "http_status": status, "error": error, "client_error": is4xx, "exception": exc,
            "message": sp.status.message or (exc or {}).get("message", ""), "origin": a.get("origin"),
            "url": a.get("http.url") or a.get("url.full") or a.get("http.target") or "",
        }
        method = a.get("http.request.method") or a.get("http.method")
        target = a.get("url.path") or a.get("http.target") or a.get("http.route")
        if span["kind"] == "server" and method and target:  # the real URL reads better than "/api/{svc}/{path}"
            span["name"] = f"{method} {target}"
        elif span["kind"] == "client" and method and span["url"]:  # "POST payments:9104/charge", not just "POST"
            u = urlparse(span["url"])
            span["name"] = f"{method} {u.netloc}{u.path}"
        t = self.traces.setdefault(sp.trace_id.hex(), {"id": sp.trace_id.hex(), "spans": {}, "seen": 0.0})
        t["spans"][span["id"]] = span
        t["seen"] = time.time()
        if span["kind"] == "server" and span["origin"] != "probe":
            self.reqs.setdefault(service, deque(maxlen=3000)).append((time.time(), span["dur_ms"], span["error"]))

    @staticmethod
    def _bucket(t):
        if any(s["error"] for s in t["spans"].values()):
            return "errors"
        return "probe" if any(s["origin"] == "probe" for s in t["spans"].values()) else "user"

    def _prune(self):
        groups: dict[str, list] = {k: [] for k in CAPS}
        for tid, t in self.traces.items():
            groups[self._bucket(t)].append((t["seen"], tid))
        for kind, items in groups.items():
            if len(items) > CAPS[kind]:
                for _, tid in sorted(items)[: len(items) - CAPS[kind]]:
                    del self.traces[tid]

    # ------------------------------------------------------------------- query
    def _summary(self, t):
        spans = list(t["spans"].values())
        start = min(s["start"] for s in spans)
        end = max(s["start"] + s["dur_ms"] / 1000 for s in spans)
        ids = {s["id"] for s in spans}
        roots = sorted((s for s in spans if not s["parent"] or s["parent"] not in ids), key=lambda s: s["start"])
        root = roots[0]
        errs = [s for s in spans if s["error"]]
        order = []  # services in the order the request reached them, not alphabetical
        for sp in sorted(spans, key=lambda x: x["start"]):
            if sp["service"] not in order:
                order.append(sp["service"])
        # The failure's origin is the earliest error span with no failing child: the deepest point of failure.
        parents_of_err = {s["parent"] for s in errs}
        leaf = next(iter(sorted((s for s in errs if s["id"] not in parents_of_err), key=lambda s: s["start"])), None)
        failing = None
        if leaf:
            failing = {"service": leaf["service"], "name": leaf["name"], "http_status": leaf["http_status"],
                       "message": (leaf["exception"] or {}).get("type") or leaf["message"] or ""}
            if leaf["kind"] == "client":  # blame the service that was being called, not the caller
                failing["calling"] = leaf["url"]
        return {"trace_id": t["id"], "start": start, "duration_ms": round((end - start) * 1000, 1),
                "root": root["name"], "root_service": root["service"],
                "origin": "probe" if any(s["origin"] == "probe" for s in spans)
                else "control" if root["service"] == "ops" else "user",
                "error": bool(errs), "spans": len(spans), "services": order,
                "failing": failing}

    def list(self, kind="errors", limit=20):
        with self.lock:
            out = []
            for t in self.traces.values():
                if not t["spans"]:
                    continue
                sm = self._summary(t)
                if kind == "all" or (kind == "errors" and sm["error"]) or kind == sm["origin"]:
                    out.append(sm)
            out.sort(key=lambda s: s["start"], reverse=True)
            return out[:limit]

    def detail(self, trace_id):
        with self.lock:
            t = self.traces.get(trace_id)
            if not t:
                return None
            spans = sorted(t["spans"].values(), key=lambda s: s["start"])
            t0 = spans[0]["start"]
            by_id = {s["id"]: s for s in spans}

            def depth(s):
                d = 0
                while s["parent"] in by_id and d < 20:
                    s, d = by_id[s["parent"]], d + 1
                return d

            return {**self._summary(t), "spans_detail": [
                {**s, "offset_ms": round((s["start"] - t0) * 1000, 2), "depth": depth(s)} for s in spans]}

    def stats(self):
        now = time.time()
        out = {}
        with self.lock:
            for svc, dq in self.reqs.items():
                recent = [r for r in dq if now - r[0] <= WINDOW]
                if not recent:
                    continue
                durs = sorted(r[1] for r in recent)
                out[svc] = {"requests_1m": sum(1 for r in recent if now - r[0] <= 60), "requests": len(recent),
                            "error_pct": round(100 * sum(1 for r in recent if r[2]) / len(recent), 1),
                            "p95_ms": durs[min(len(durs) - 1, int(0.95 * len(durs)))],
                            "p50_ms": durs[len(durs) // 2]}
        return out

    def counts(self, service):
        """(requests, errors) of real user traffic seen by a service so far (probe traffic excluded)."""
        with self.lock:
            dq = self.reqs.get(service)
            return (len(dq), sum(1 for r in dq if r[2])) if dq else (0, 0)

    def failure_hint(self, service, max_chars=450):
        """One short description of the newest failing trace involving `service`, for the repair prompt."""
        with self.lock:
            cands = [t for t in self.traces.values()
                     if any(s["error"] and s["service"] == service for s in t["spans"].values())]
            if not cands:
                return ""
            sm = self._summary(max(cands, key=lambda t: t["seen"]))
        f = sm["failing"]
        if not f:
            return ""
        hint = (f"OpenTelemetry trace {sm['trace_id'][:8]}: {sm['root']} across {', '.join(sm['services'])}; "
                f"failing span in {f['service']}: {f['name']}"
                + (f" -> HTTP {f['http_status']}" if f["http_status"] else "")
                + (f" ({f['message']})" if f["message"] else "")
                + (f" while calling {f['calling']}" if f.get("calling") else ""))
        return hint[:max_chars]


store = TraceStore()
