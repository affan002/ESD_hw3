"""The gRPC server as it runs in a replica: started by the app's lifespan in the same
process (A3-2), on a free port, sharing the temporary database and fault state with HTTP."""

import logging
from datetime import datetime, timedelta, timezone

import grpc
import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.grpc_gen import sensor_ingest_pb2 as pb
from app.grpc_gen import sensor_ingest_pb2_grpc as pb_grpc
from app.main import create_app
from tests.test_metrics import sample
from tests.test_readings import reading


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "shared.db"))
    monkeypatch.setenv("API_HOST", "127.0.0.1")
    monkeypatch.setenv("GRPC_ENABLED", "true")
    monkeypatch.setenv("GRPC_PORT", "0")  # any free port; the app reports it in app.state.grpc_port
    app = create_app(load_settings())
    with TestClient(app) as http:
        channel = grpc.insecure_channel(f"127.0.0.1:{app.state.grpc_port}")
        stub = pb_grpc.IngestServiceStub(channel)
        stub.channel = channel  # for other stubs on the same server (health checks)
        yield stub, http
        channel.close()


def req(**overrides):
    body = reading(**overrides)
    return pb.SubmitReadingRequest(**body)


def call(stub, request, request_id="test-grpc-1"):
    return stub.SubmitReading.with_call(request, timeout=5, metadata=(("x-request-id", request_id),))


def test_submit_reading_ok_and_request_id_echoed(env, caplog):
    stub, _ = env
    caplog.set_level(logging.INFO)
    before = sample("sensor_grpc_requests_total", method="SubmitReading", code="OK")
    resp, c = call(stub, req(temperature_c=40.0))
    assert resp.reading.id >= 1 and not resp.replayed
    assert list(resp.reading.anomaly_reasons) == ["temperature_high"]
    assert dict(c.initial_metadata())["x-request-id"] == "test-grpc-1"
    assert sample("sensor_grpc_requests_total", method="SubmitReading", code="OK") - before == 1
    access = [r for r in caplog.records if getattr(r, "event", None) == "grpc_request"][-1]
    assert (access.rpc_method, access.grpc_code) == ("SubmitReading", "OK")
    stored = [r for r in caplog.records if getattr(r, "event", None) == "reading_stored"][-1]
    assert stored.transport == "grpc"


def test_invalid_reading_is_invalid_argument_and_counted_as_rejected(env):
    stub, _ = env
    rejected = sample("sensor_readings_total", transport="grpc", outcome="rejected")
    with pytest.raises(grpc.RpcError) as err:
        call(stub, req(humidity_pct=150))
    assert err.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "humidity_pct" in err.value.details()
    assert sample("sensor_readings_total", transport="grpc", outcome="rejected") - rejected == 1


def test_missing_field_is_invalid_not_zero(env):
    stub, _ = env
    body = reading()
    body.pop("battery_pct")
    with pytest.raises(grpc.RpcError) as err:
        call(stub, pb.SubmitReadingRequest(**body))
    assert err.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "battery_pct: Field required" in err.value.details()


def test_future_timestamp_is_invalid_argument(env):
    stub, _ = env
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    with pytest.raises(grpc.RpcError) as err:
        call(stub, req(timestamp=future))
    assert err.value.code() == grpc.StatusCode.INVALID_ARGUMENT


def test_same_rules_and_database_as_http(env):
    """A reading stored over HTTP is a replay over gRPC, and a changed one is a conflict."""
    stub, http = env
    body = reading()
    created = http.post("/readings", json=body)
    assert created.status_code == 201
    resp, _ = call(stub, pb.SubmitReadingRequest(**body))
    assert resp.replayed and resp.reading.id == created.json()["id"]
    with pytest.raises(grpc.RpcError) as err:
        call(stub, pb.SubmitReadingRequest(**{**body, "temperature_c": 30.0}))
    assert err.value.code() == grpc.StatusCode.ALREADY_EXISTS
    assert http.get("/devices/dev-001").json()["reading_count"] == 1


def test_rate_limit_is_resource_exhausted(env):
    stub, _ = env
    codes = []
    for _ in range(6):
        try:
            call(stub, req(device_id="dev-050"))
            codes.append("OK")
        except grpc.RpcError as err:
            codes.append(err.code().name)
            assert dict(err.trailing_metadata())["retry-after"] == "1"
    assert codes == ["OK"] * 5 + ["RESOURCE_EXHAUSTED"]


def test_deadline_exceeded_on_the_client(env):
    stub, _ = env
    with pytest.raises(grpc.RpcError) as err:
        stub.SubmitReading(req(), timeout=0.000001)
    assert err.value.code() == grpc.StatusCode.DEADLINE_EXCEEDED


# ---------- A3: GetDevice (read-only test traffic), replica metadata, health checks ----------


def get_device(stub, device_id, request_id="test-grpc-get"):
    return stub.GetDevice.with_call(pb.GetDeviceRequest(device_id=device_id), timeout=5,
                                    metadata=(("x-request-id", request_id),))


def test_get_device_matches_http_and_names_the_replica(env):
    stub, http = env
    http.post("/readings", json=reading(device_id="dev-060"))
    before = sample("sensor_grpc_requests_total", method="GetDevice", code="OK")
    device, c = get_device(stub, "dev-060")
    metadata = dict(c.initial_metadata())
    assert metadata["x-replica"] == "T" and metadata["x-request-id"] == "test-grpc-get"
    body = http.get("/devices/dev-060").json()
    assert (device.device_id, device.status, device.reading_count) == (body["device_id"], body["status"], 1)
    assert device.latest_reading.id == body["latest_reading"]["id"]
    assert sample("sensor_grpc_requests_total", method="GetDevice", code="OK") - before == 1


@pytest.mark.parametrize("device_id, code", [("dev-999", "NOT_FOUND"), ("nope", "INVALID_ARGUMENT")])
def test_get_device_errors(env, device_id, code):
    stub, _ = env
    with pytest.raises(grpc.RpcError) as err:
        get_device(stub, device_id)
    assert err.value.code().name == code


def test_faults_set_over_http_apply_to_get_device(env):
    """One process, one fault state: delaying a replica delays both interfaces (A3-2, A3-14)."""
    stub, http = env
    http.post("/readings", json=reading(device_id="dev-061"))
    http.put("/admin/faults", json={"error_every_n": 1})
    with pytest.raises(grpc.RpcError) as err:
        get_device(stub, "dev-061")
    assert err.value.code() == grpc.StatusCode.INTERNAL
    http.delete("/admin/faults")
    assert get_device(stub, "dev-061")[0].device_id == "dev-061"


def test_health_service_serving_and_not_in_app_metrics(env):
    from grpc_health.v1 import health_pb2, health_pb2_grpc

    stub, http = env
    health = health_pb2_grpc.HealthStub(stub.channel)
    before = sum(sample("sensor_grpc_requests_total", method=m, code="OK") for m in ("SubmitReading", "GetDevice"))
    for service in ("", "sensor.v1.IngestService"):
        resp = health.Check(health_pb2.HealthCheckRequest(service=service), timeout=5)
        assert resp.status == health_pb2.HealthCheckResponse.SERVING
    after = sum(sample("sensor_grpc_requests_total", method=m, code="OK") for m in ("SubmitReading", "GetDevice"))
    assert after == before
