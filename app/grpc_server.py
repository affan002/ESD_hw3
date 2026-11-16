"""gRPC server: SubmitReading (A2) and GetDevice (A3 test traffic).

Since A3 it runs inside each replica's API process, started by the FastAPI lifespan
(app/main.py, decision A3-2), on port 50051 next to HTTP on 8000. It shares that
process's metrics registry and fault state. `python -m app.grpc_server` still runs
it on its own (with its own /metrics on GRPC_METRICS_PORT) for local experiments.
It applies the same business rules as HTTP (app/ingest.py, app/anomaly.py) to the
same SQLite database and never calls the HTTP API.

Call path: client stub (IngestServiceStub, generated) → channel → (Envoy) → this server →
ObservabilityInterceptor (request ID, replica, access log, metrics) → IngestServicer
→ ingest.submit_reading / db.get_device → SQLite.

The standard grpc.health.v1 service is registered too, for Envoy's active health
checks (decision A3-12); the interceptor leaves those calls out of logs and metrics.
"""

import logging
import re
import signal
import time
import uuid
from concurrent import futures

import grpc
from fastapi import HTTPException
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from prometheus_client import start_http_server
from pydantic import ValidationError

from app import db, ingest, metrics
from app.anomaly import device_status
from app.config import Settings, load_settings
from app.faults import FaultState
from app.grpc_gen import sensor_ingest_pb2 as pb
from app.grpc_gen import sensor_ingest_pb2_grpc as pb_grpc
from app.logging_setup import request_id_var, set_replica, setup_logging
from app.middleware import VALID_REQUEST_ID
from app.models import DEVICE_ID_PATTERN, ReadingIn, ReadingOut
from app.ratelimit import RateLimiter
from app.replica import resolve_replica_id

log = logging.getLogger("app.grpc")

REQUEST_ID_KEY = "x-request-id"  # gRPC metadata keys are lowercase
REPLICA_KEY = "x-replica"  # A3: which replica answered (Envoy logs it as %RESP(X-REPLICA)%)
SERVICE_NAME = "sensor.v1.IngestService"
_DEVICE_ID_RE = re.compile(DEVICE_ID_PATTERN)
_NUMERIC_FIELDS = ("temperature_c", "humidity_pct", "battery_pct")


class ObservabilityInterceptor(grpc.ServerInterceptor):
    """The gRPC counterpart of RequestContextMiddleware: for every unary call it takes
    (or creates) the request ID, echoes it back in the response metadata, and records
    one `grpc_request` log line plus the gRPC metrics. Health checks pass straight through."""

    def __init__(self, replica: str | None = None):
        self.replica = replica

    def intercept_service(self, continuation, handler_call_details):
        handler = continuation(handler_call_details)
        if handler is None or handler.unary_unary is None:
            return handler  # unknown method: gRPC answers UNIMPLEMENTED itself
        if not handler_call_details.method.startswith(f"/{SERVICE_NAME}/"):
            return handler  # grpc.health.v1 probes from Envoy every 2 s: not application traffic
        method = handler_call_details.method.rsplit("/", 1)[-1]  # "/sensor.v1.IngestService/SubmitReading"
        incoming = dict(handler_call_details.invocation_metadata or ()).get(REQUEST_ID_KEY, "")
        inner = handler.unary_unary

        def wrapper(request, context):
            request_id = incoming if VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
            token = request_id_var.set(request_id)
            response_metadata = [(REQUEST_ID_KEY, request_id)]
            if self.replica:
                response_metadata.append((REPLICA_KEY, self.replica))
            context.send_initial_metadata(tuple(response_metadata))
            metrics.GRPC_IN_PROGRESS.labels(method).inc()
            start = time.perf_counter()
            try:
                return inner(request, context)
            except Exception as exc:
                if context.code() is None:  # not an abort(): a real crash
                    log.error("unhandled exception", exc_info=True,
                              extra={"event": "unhandled_exception", "error": type(exc).__name__})
                    context.abort(grpc.StatusCode.INTERNAL, "internal server error")
                raise
            finally:
                duration_s = time.perf_counter() - start
                code = (context.code() or grpc.StatusCode.OK).name
                metrics.GRPC_IN_PROGRESS.labels(method).dec()
                metrics.GRPC_REQUESTS.labels(method, code).inc()
                metrics.GRPC_DURATION.labels(method).observe(duration_s)
                extra = {"event": "grpc_request", "rpc_method": method, "grpc_code": code,
                         "duration_ms": round(duration_s * 1000, 2)}
                if code != "OK":
                    extra["grpc_details"] = context.details()
                log.log(logging.ERROR if code in ("INTERNAL", "UNKNOWN") else logging.INFO,
                        "%s -> %s", method, code, extra=extra)
                request_id_var.reset(token)

        return grpc.unary_unary_rpc_method_handler(
            wrapper,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )


def _to_pb(r: ReadingOut) -> pb.Reading:
    return pb.Reading(
        id=r.id, device_id=r.device_id, timestamp=r.timestamp, received_at=r.received_at,
        temperature_c=r.temperature_c, humidity_pct=r.humidity_pct, battery_pct=r.battery_pct,
        is_anomaly=r.is_anomaly, anomaly_reasons=r.anomaly_reasons,
    )


def _device_to_pb(device: dict, status: str) -> pb.Device:
    return pb.Device(
        device_id=device["device_id"], status=status, first_seen=device["first_seen"],
        last_seen=device["last_seen"], reading_count=device["reading_count"],
        anomaly_count=device["anomaly_count"], latest_reading=_to_pb(ReadingOut(**device["latest_reading"])),
    )


def _describe(errors: list[dict]) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in errors)


class IngestServicer(pb_grpc.IngestServiceServicer):
    """Implements the generated IngestServiceServicer base class."""

    def __init__(self, settings: Settings, faults: FaultState):
        self.settings = settings
        self.faults = faults
        self.limiter = RateLimiter(settings.rate_limit_burst, settings.rate_limit_per_s)

    def GetDevice(self, request: pb.GetDeviceRequest, context: grpc.ServicerContext) -> pb.Device:
        """gRPC counterpart of GET /devices/{device_id}: same query, same status rule, same faults."""
        if not _DEVICE_ID_RE.match(request.device_id):
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"device_id: must match {DEVICE_ID_PATTERN}")
        try:
            self.faults.apply()  # may sleep (delay fault) or raise (error fault)
        except HTTPException:
            context.abort(grpc.StatusCode.INTERNAL, "injected fault")
        conn = db.connect(self.settings.db_path)
        try:
            device = db.get_device(conn, request.device_id)
        finally:
            conn.close()
        if device is None:
            context.abort(grpc.StatusCode.NOT_FOUND, "device not found")
        return _device_to_pb(device, device_status(device, self.settings.stale_after_s))

    def SubmitReading(self, request: pb.SubmitReadingRequest, context: grpc.ServicerContext) -> pb.SubmitReadingResponse:
        # Only fields that were actually sent; a missing number is a validation error, not 0.0.
        data = {"device_id": request.device_id, "timestamp": request.timestamp}
        data.update({f: getattr(request, f) for f in _NUMERIC_FIELDS if request.HasField(f)})
        device_id = request.device_id if _DEVICE_ID_RE.match(request.device_id) else None
        try:
            reading = ReadingIn.model_validate(data)  # the same model, so the same physical limits, as HTTP
        except ValidationError as exc:
            errors = [{"loc": e["loc"], "type": e["type"], "msg": e["msg"]} for e in exc.errors()]
            ingest.record_rejection(errors, device_id, "grpc")
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, _describe(errors))

        retry_after = self.limiter.check(reading.device_id, "SubmitReading")
        if retry_after is not None:
            context.set_trailing_metadata((("retry-after", str(retry_after)),))
            context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED,
                          f"device {reading.device_id} is over its rate limit; retry in {retry_after} s")

        conn = db.connect(self.settings.db_path)
        try:
            result = ingest.submit_reading(conn, reading, self.settings, transport="grpc")
        except ingest.FutureTimestamp as exc:
            ingest.record_rejection([{"loc": ("timestamp",), "type": exc.type}], reading.device_id, "grpc")
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"timestamp: {exc}")
        except ingest.ReadingConflict as exc:
            context.abort(grpc.StatusCode.ALREADY_EXISTS, str(exc))
        finally:
            conn.close()
        return pb.SubmitReadingResponse(reading=_to_pb(result.reading), replayed=not result.created)


class GrpcServer:
    """A started server plus its health service, so shutdown can report NOT_SERVING first."""

    def __init__(self, server: grpc.Server, health_servicer: health.HealthServicer, port: int):
        self.server = server
        self.health = health_servicer
        self.port = port

    def stop(self, grace: float | None = None) -> None:
        self.health.enter_graceful_shutdown()  # health checks now answer NOT_SERVING
        self.server.stop(grace).wait()

    def wait_for_termination(self) -> None:
        self.server.wait_for_termination()


def serve(settings: Settings, port: int | None = None, faults: FaultState | None = None,
          replica: str | None = None) -> GrpcServer:
    """Start the server (non-blocking) on `port` (default GRPC_PORT; 0 picks a free one).
    `faults` is shared with the HTTP app when both run in one process."""
    db.init_schema(settings.db_path)
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10),
                         interceptors=[ObservabilityInterceptor(replica)])
    pb_grpc.add_IngestServiceServicer_to_server(IngestServicer(settings, faults or FaultState()), server)
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    bound = server.add_insecure_port(f"{settings.api_host}:{settings.grpc_port if port is None else port}")
    server.start()
    for service in ("", SERVICE_NAME):  # "" = the server as a whole (what Envoy checks)
        health_servicer.set(service, health_pb2.HealthCheckResponse.SERVING)
    return GrpcServer(server, health_servicer, bound)


def main() -> None:
    """Standalone gRPC server (local experiments); in Docker it runs inside `python -m app`."""
    settings = load_settings()
    setup_logging("sensor-grpc", settings.log_level)
    replica = resolve_replica_id(settings.replica_id)
    set_replica(replica)
    start_http_server(settings.grpc_metrics_port)  # its own /metrics, since there is no HTTP app here
    metrics.REPLICA_INFO.labels(replica).set(1)
    server = serve(settings, replica=replica)
    log.info("sensor-grpc starting", extra={"event": "startup", "config": {
        "db_path": settings.db_path, "grpc_port": server.port, "metrics_port": settings.grpc_metrics_port}})

    def stop(signum, _frame):
        log.info("sensor-grpc stopping", extra={"event": "shutdown"})
        server.stop(grace=5)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    server.wait_for_termination()


if __name__ == "__main__":
    main()
