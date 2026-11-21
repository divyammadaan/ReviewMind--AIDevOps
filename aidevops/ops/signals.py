"""OTLP metrics and logs receivers for the control plane (traces live in telemetry.py).

Metrics: per-service HTTP server histograms, which arrive as cumulative totals, are turned into rates and
latency percentiles over a time window. Logs: records from every service, each stamped with the trace and
span ID when it was emitted inside a request.
"""
import gzip
import threading
import time
from collections import deque

from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest

from telemetry import _attrs, _val


def _percentile(bounds, counts, q):
    """Estimate a latency percentile (ms) from histogram bucket counts, interpolating inside the bucket."""
    total = sum(counts)
    if total <= 0:
        return None
    rank, seen = q * total, 0
    for i, c in enumerate(counts):
        if seen + c >= rank and c > 0:
            if i >= len(bounds):  # the open-ended last bucket: all we know is "above the last bound"
                return bounds[-1] if bounds else None
            lo = bounds[i - 1] if i > 0 else 0.0
            return lo + (bounds[i] - lo) * ((rank - seen) / c)
        seen += c
    return bounds[-1] if bounds else None


def _r(v):
    return None if v is None else round(v, 1)


class MetricStore:
    """Per-service HTTP server metrics, from OTLP histograms, turned into rates over a window."""

    HISTORY = 240  # snapshots per service (one every ~5 s)

    def __init__(self):
        self.lock = threading.Lock()
        self.hist: dict[str, deque] = {}
        self.active: dict[str, float] = {}
        self.client: dict[str, dict] = {}
        self.running: dict[str, dict] = {}  # running totals for services that report delta temporality
        self.received = 0
        self.last_received = None

    def ingest(self, body: bytes, encoding: str = "") -> int:
        if encoding == "gzip":
            body = gzip.decompress(body)
        req = ExportMetricsServiceRequest()
        req.ParseFromString(body)
        n = 0
        with self.lock:
            for rm in req.resource_metrics:
                service = _attrs(rm.resource.attributes).get("service.name", "unknown")
                snap = {"count": 0, "err": 0, "sum": 0.0, "buckets": None, "bounds": None}
                got_server = False
                for sm in rm.scope_metrics:
                    for m in sm.metrics:
                        n += 1
                        which = m.WhichOneof("data")
                        scale = 1000.0 if m.unit == "s" else 1.0  # we show milliseconds
                        if which == "histogram" and "http.server" in m.name and "duration" in m.name:
                            got_server = True
                            self._server_hist(m, scale, snap, service)
                        elif which == "histogram" and "http.client" in m.name and "duration" in m.name:
                            c = self.client.setdefault(service, {"count": 0, "sum": 0.0})
                            c["count"] = sum(dp.count for dp in m.histogram.data_points)
                            c["sum"] = sum(dp.sum for dp in m.histogram.data_points) * scale
                        elif which == "sum" and "http.server" in m.name and "active" in m.name:
                            self.active[service] = sum(dp.as_int or dp.as_double for dp in m.sum.data_points)
                if got_server:
                    self._record(service, snap)
            self.received += n
            self.last_received = time.time()
        return n

    def _server_hist(self, m, scale, snap, service):
        delta = m.histogram.aggregation_temporality == 1
        for dp in m.histogram.data_points:
            a = _attrs(dp.attributes)
            status = a.get("http.response.status_code", a.get("http.status_code"))
            snap["count"] += dp.count
            snap["sum"] += dp.sum * scale
            if isinstance(status, int) and status >= 500:
                snap["err"] += dp.count
            bounds = [b * scale for b in dp.explicit_bounds]
            counts = list(dp.bucket_counts)
            if snap["buckets"] is None:
                snap["buckets"], snap["bounds"] = counts, bounds
            elif len(counts) == len(snap["buckets"]):
                snap["buckets"] = [x + y for x, y in zip(snap["buckets"], counts)]
        if delta:  # turn per-interval numbers into running totals so the rest can assume cumulative
            run = self.running.setdefault(service, {"count": 0, "err": 0, "sum": 0.0, "buckets": None})
            run["count"] += snap["count"]
            run["err"] += snap["err"]
            run["sum"] += snap["sum"]
            if snap["buckets"]:
                prev = run["buckets"] or [0] * len(snap["buckets"])
                if len(prev) == len(snap["buckets"]):
                    run["buckets"] = [x + y for x, y in zip(prev, snap["buckets"])]
            snap.update(count=run["count"], err=run["err"], sum=run["sum"], buckets=run["buckets"])

    def _record(self, service, snap):
        dq = self.hist.setdefault(service, deque(maxlen=self.HISTORY))
        if dq and snap["count"] < dq[-1]["count"]:  # the service restarted: its counters began again from zero
            dq.clear()
        dq.append({"t": time.time(), **snap})

    def stats(self, window=60):
        now, out = time.time(), {}
        with self.lock:
            for svc, dq in self.hist.items():
                if not dq or now - dq[-1]["t"] > 30:  # stale: the service stopped reporting
                    continue
                last = dq[-1]
                first = next((s for s in dq if now - s["t"] <= window), dq[0])
                dt = max(last["t"] - first["t"], 1e-9)
                dc = last["count"] - first["count"]
                row = {"total": last["count"], "active": self.active.get(svc, 0), "window_s": round(dt, 1)}
                if dc > 0 and first is not last and last["buckets"] and first["buckets"] \
                        and len(last["buckets"]) == len(first["buckets"]):
                    de = last["err"] - first["err"]
                    counts = [a - b for a, b in zip(last["buckets"], first["buckets"])]
                    row.update(rate=round(dc / dt, 2), error_pct=round(100 * de / dc, 1),
                               avg_ms=round((last["sum"] - first["sum"]) / dc, 1),
                               p50_ms=_r(_percentile(last["bounds"], counts, 0.5)),
                               p95_ms=_r(_percentile(last["bounds"], counts, 0.95)),
                               p99_ms=_r(_percentile(last["bounds"], counts, 0.99)))
                else:
                    row.update(rate=0.0, error_pct=0.0, avg_ms=None, p50_ms=None, p95_ms=None, p99_ms=None)
                out[svc] = row
        return out


SEV = [(21, "FATAL"), (17, "ERROR"), (13, "WARN"), (9, "INFO"), (5, "DEBUG"), (1, "TRACE")]


def _sev_name(n):
    return next((name for floor, name in SEV if n >= floor), "INFO")


class LogStore:
    """OTLP log records from every service. Records emitted inside a request carry its trace and span ID."""

    def __init__(self):
        self.lock = threading.Lock()
        self.important: deque = deque(maxlen=1500)  # WARN and above
        self.routine: deque = deque(maxlen=2500)  # INFO and below (mostly access logs)
        self.received = 0
        self.last_received = None

    def ingest(self, body: bytes, encoding: str = "") -> int:
        if encoding == "gzip":
            body = gzip.decompress(body)
        req = ExportLogsServiceRequest()
        req.ParseFromString(body)
        n = 0
        with self.lock:
            for rl in req.resource_logs:
                service = _attrs(rl.resource.attributes).get("service.name", "unknown")
                for sl in rl.scope_logs:
                    for lr in sl.log_records:
                        a = _attrs(lr.attributes)
                        sev = lr.severity_number or 9
                        rec = {"t": (lr.time_unix_nano or lr.observed_time_unix_nano) / 1e9, "service": service,
                               "level": lr.severity_text or _sev_name(sev), "sev": sev, "logger": sl.scope.name,
                               "message": str(_val(lr.body)) if lr.HasField("body") else "",
                               "trace_id": lr.trace_id.hex() if lr.trace_id.strip(b"\0") else None,
                               "span_id": lr.span_id.hex() if lr.span_id.strip(b"\0") else None,
                               "exception": a.get("exception.type"), "stacktrace": a.get("exception.stacktrace")}
                        (self.important if sev >= 13 else self.routine).append(rec)
                        n += 1
            self.received += n
            self.last_received = time.time()
        return n

    def query(self, service=None, level="warn", trace_id=None, q=None, limit=100):
        with self.lock:
            src = list(self.important) + (list(self.routine) if level == "all" or trace_id else [])
        out = []
        for r in src:
            if service and r["service"] != service:
                continue
            if trace_id and r["trace_id"] != trace_id:
                continue
            if q and q.lower() not in (r["message"] + (r["stacktrace"] or "")).lower():
                continue
            out.append(r)
        out.sort(key=lambda r: r["t"], reverse=True)
        return out[:limit]

    def around(self, services, start, end, pad=1.0, limit=40):
        """Logs from the given services in a time window: the fallback for records with no trace ID."""
        with self.lock:
            src = list(self.important) + list(self.routine)
        out = [r for r in src if r["service"] in services and start - pad <= r["t"] <= end + pad and not r["trace_id"]]
        out.sort(key=lambda r: r["t"])
        return out[:limit]


metric_store = MetricStore()
log_store = LogStore()
