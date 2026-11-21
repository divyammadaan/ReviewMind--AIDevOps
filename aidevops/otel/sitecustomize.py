"""OpenTelemetry bootstrap (traces, metrics, logs), loaded by Python in every process via PYTHONPATH=/otel.

It instruments FastAPI (incoming requests) and httpx (outgoing requests) without touching the service
code, which matters because the services are tiny files that the AI edits and the fault injector mutates.
  * traces   : trace context (and a small "origin" baggage item) is propagated over HTTP headers
  * metrics  : the instrumentations record request duration / count / in-flight, exported every 5 s
  * logs     : Python logging (including uvicorn's access and error logs) is exported, stamped with the
               trace/span ID when emitted inside a request

Telemetry is never a hard dependency: any failure here is swallowed and the process starts normally, and
everything is exported from background threads with short timeouts and bounded queues, so an unreachable
collector cannot block requests.
"""
import logging
import logging.config
import os
import time


class _RateLimit(logging.Filter):
    """Exporter errors repeat on every batch while the collector is down: log one per minute."""

    def __init__(self):
        super().__init__()
        self.last = 0.0

    def filter(self, record):
        if record.levelno < logging.WARNING:
            return True
        now = time.time()
        if now - self.last < 60:
            return False
        self.last = now
        return True


class _NoSelfTelemetry(logging.Filter):
    """Keep telemetry from describing itself. The control plane is the collector: if it exported its own
    access logs, every received batch would create a new access log, which would be exported, and so on."""

    def __init__(self, service):
        super().__init__()
        self.service = service

    def filter(self, record):
        if record.name.startswith("opentelemetry"):
            return False
        return not (self.service == "ops" and record.name == "uvicorn.access")


def _setup():
    name = os.environ.get("OTEL_SERVICE_NAME")
    if not name or os.environ.get("OTEL_SDK_DISABLED", "").lower() == "true":
        return
    from opentelemetry import baggage, metrics, trace
    from opentelemetry._logs import set_logger_provider
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    class OriginProcessor(SpanProcessor):
        """Copy the propagated `origin` baggage (e.g. "probe") onto every span, in every service."""

        def on_start(self, span, parent_context=None):
            origin = baggage.get_baggage("origin", parent_context)
            if origin:
                span.set_attribute("origin", str(origin))

    flt = _RateLimit()
    for logger in ("opentelemetry.exporter.otlp.proto.http.trace_exporter",
                   "opentelemetry.exporter.otlp.proto.http.metric_exporter",
                   "opentelemetry.exporter.otlp.proto.http._log_exporter",
                   "opentelemetry.sdk.trace.export", "opentelemetry.sdk.metrics._internal.export",
                   "opentelemetry.sdk._logs._internal.export"):
        logging.getLogger(logger).addFilter(flt)

    resource = Resource.create({"service.name": name})

    # -- traces
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(OriginProcessor())
    tracer_provider.add_span_processor(BatchSpanProcessor(
        OTLPSpanExporter(), schedule_delay_millis=1000, export_timeout_millis=2000, max_queue_size=1024))
    trace.set_tracer_provider(tracer_provider)

    # -- metrics (must be set before the instrumentors, which create their instruments at instrument time)
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[
        PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=5000, export_timeout_millis=2000)]))

    # -- logs
    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(
        OTLPLogExporter(), schedule_delay_millis=1000, export_timeout_millis=2000, max_queue_size=1024))
    set_logger_provider(logger_provider)
    handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
    handler.addFilter(_NoSelfTelemetry(name))

    def attach():
        # uvicorn.error propagates to "uvicorn", so attaching to "uvicorn" and "uvicorn.access" (which does
        # not propagate) covers both without duplicates; the root logger covers everything else.
        for logger_name in ("", "uvicorn", "uvicorn.access"):
            lg = logging.getLogger(logger_name)
            if handler not in lg.handlers:
                lg.addHandler(handler)

    attach()
    original_dict_config = logging.config.dictConfig

    def dict_config(*args, **kwargs):  # uvicorn replaces its loggers' handlers when it starts: re-attach
        result = original_dict_config(*args, **kwargs)
        attach()
        return result

    logging.config.dictConfig = dict_config

    try:  # drop the per-message ASGI send/receive spans: they only add noise to a trace
        FastAPIInstrumentor().instrument(exclude_spans=["receive", "send"])
    except TypeError:
        FastAPIInstrumentor().instrument()
    HTTPXClientInstrumentor().instrument()


try:
    _setup()
except Exception:  # telemetry must never stop a service from starting
    pass
