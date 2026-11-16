#!/usr/bin/env python3
"""Fixed-rate test client for Assignment 3: HTTP GET /devices/{id} or gRPC GetDevice through Envoy.

Open loop (decision A3-13): request n is started at start + n/rate whatever the
replies are doing, so a slow replica can't lower the offered load. HTTP requests
run in a thread pool, each on a new connection; gRPC calls are non-blocking
`.future()` calls on ONE channel (one HTTP/2 connection to Envoy), so Envoy has to
balance per call. No retries, no circuit breaker: every failure is counted.

Each request carries X-Request-ID / x-request-id `lb-<run>-<iface>-NNNNN`, so a run
can be found in Kibana, and the reply's `x-replica` says which replica answered.
Writes one JSON summary (also printed): rate, sent, successes, failures, status
counts, client latency percentiles, client-side replica shares, and a per-second
timeline (for the failure experiment).

    .venv/bin/python scripts/lb_load.py --interface http --run baseline --out docs/results/a3/b_baseline_http.json
    .venv/bin/python scripts/lb_load.py --interface grpc --run baseline --rate 50 --duration 120
    .venv/bin/python scripts/lb_load.py --seed          # create dev-001…dev-020 first (idempotent)
"""

import argparse
import json
import logging
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import grpc  # noqa: E402

from app.grpc_gen import sensor_ingest_pb2 as pb  # noqa: E402
from app.grpc_gen import sensor_ingest_pb2_grpc as pb_grpc  # noqa: E402
from load import percentile, utc  # noqa: E402  (same helpers as the A1 E1 load script)

log = logging.getLogger("lb_load")

# A fixed timestamp makes --seed idempotent: a second seed is a 200 replay, not new data.
SEED_TIMESTAMP = "2026-11-16T00:00:00.000Z"


class Results:
    """Thread-safe store of (start offset s, latency s, status, replica) per request."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.rows: list[tuple[float, float, str, str]] = []

    def add(self, started: float, latency: float, status: str, replica: str | None) -> None:
        with self.lock:
            self.rows.append((started, latency, status, replica or "-"))


def device_for(n: int, devices: int) -> str:
    return f"dev-{1 + n % devices:03d}"


def run_http(args, schedule, results: Results, t0: float) -> None:
    def send(n: int) -> None:
        req = urllib.request.Request(
            f"{args.http_url}/devices/{device_for(n, args.devices)}",
            headers={"X-Request-ID": f"lb-{args.run}-http-{n:05d}"},
        )
        started = time.monotonic()
        replica = None
        try:
            with urllib.request.urlopen(req, timeout=args.timeout) as resp:
                status, replica = str(resp.status), resp.headers.get("x-replica")
        except urllib.error.HTTPError as exc:
            status, replica = str(exc.code), exc.headers.get("x-replica")
        except Exception as exc:  # timeout or connection error: no HTTP status
            status = "timeout" if "timed out" in str(exc) else "conn_error"
        results.add(started - t0, time.monotonic() - started, status, replica)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        schedule(lambda n: pool.submit(send, n))
    # leaving the pool waits for the last requests


def run_grpc(args, schedule, results: Results, t0: float) -> None:
    channel = grpc.insecure_channel(args.grpc_target)
    grpc.channel_ready_future(channel).result(timeout=5)  # connect once, before the clock matters
    stub = pb_grpc.IngestServiceStub(channel)
    pending = threading.Semaphore(0)
    sent = [0]

    def send(n: int) -> None:
        started = time.monotonic()
        future = stub.GetDevice.future(
            pb.GetDeviceRequest(device_id=device_for(n, args.devices)),
            timeout=args.timeout,
            metadata=(("x-request-id", f"lb-{args.run}-grpc-{n:05d}"),),
        )

        def done(f) -> None:
            latency = time.monotonic() - started
            code = f.code()
            try:
                replica = dict(f.initial_metadata() or ()).get("x-replica")
            except Exception:
                replica = None
            results.add(started - t0, latency, code.name if code else "UNKNOWN", replica)
            pending.release()

        future.add_done_callback(done)
        sent[0] += 1

    schedule(send)
    for _ in range(sent[0]):
        pending.acquire()
    channel.close()


def seed(args) -> None:
    """One reading per device (dev-001…), all with the same fixed timestamp."""
    for i in range(args.devices):
        body = json.dumps({"device_id": device_for(i, args.devices), "timestamp": SEED_TIMESTAMP,
                           "temperature_c": 22.0, "humidity_pct": 45.0, "battery_pct": 88.0}).encode()
        req = urllib.request.Request(f"{args.http_url}/readings", data=body, method="POST",
                                     headers={"Content-Type": "application/json", "X-Request-ID": f"lb-seed-{i:03d}"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            log.info("seeded %s -> %s", device_for(i, args.devices), resp.status)


def summarise(args, results: Results, sent: int, start_wall: float, end_wall: float, send_span_s: float) -> dict:
    rows = sorted(results.rows)
    ok_status = "200" if args.interface == "http" else "OK"
    statuses = Counter(r[2] for r in rows)
    replicas = Counter(r[3] for r in rows if r[2] == ok_status)
    ok = statuses.get(ok_status, 0)
    latencies = sorted(r[1] * 1000 for r in rows)
    ok_latencies = sorted(r[1] * 1000 for r in rows if r[2] == ok_status)

    def pcts(values: list[float]) -> dict:
        if not values:
            return {}
        return {"p50": round(percentile(values, 0.50), 1), "p95": round(percentile(values, 0.95), 1),
                "p99": round(percentile(values, 0.99), 1), "max": round(values[-1], 1),
                "mean": round(sum(values) / len(values), 1)}

    timeline = []
    for second in range(int(args.duration) + 1):
        bucket = [r for r in rows if second <= r[0] < second + 1]
        if not bucket:
            continue
        timeline.append({
            "t": second, "sent": len(bucket),
            "ok": sum(1 for r in bucket if r[2] == ok_status),
            "failed": sum(1 for r in bucket if r[2] != ok_status),
            "by_replica": dict(Counter(r[3] for r in bucket if r[2] == ok_status)),
            "p95_ms": round(percentile(sorted(r[1] * 1000 for r in bucket), 0.95), 1),
        })
    return {
        "run": args.run,
        "interface": args.interface,
        "operation": "GET /devices/{device_id}" if args.interface == "http" else "GetDevice",
        "target": args.http_url if args.interface == "http" else args.grpc_target,
        "start_utc": utc(start_wall),
        "end_utc": utc(end_wall),
        "target_rps": args.rate,
        "duration_s": args.duration,
        "actual_rps": round(sent / send_span_s, 2) if send_span_s > 0 else None,
        "sent": sent,
        "completed": len(rows),
        "successes": ok,
        "failures": len(rows) - ok,
        "error_rate_pct": round(100 * (len(rows) - ok) / len(rows), 2) if rows else None,
        "status_counts": dict(statuses),
        "replica_shares_pct": {k: round(100 * v / ok, 1) for k, v in sorted(replicas.items())} if ok else {},
        "replica_counts": dict(sorted(replicas.items())),
        "client_latency_ms": pcts(latencies),
        "client_latency_ok_ms": pcts(ok_latencies),
        "client": {"timeout_s": args.timeout, "retries": 0, "circuit_breaker": False,
                   "devices": args.devices, "connections": "one per request" if args.interface == "http" else "one channel"},
        "timeline": timeline,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--interface", choices=("http", "grpc"), default="http")
    p.add_argument("--run", default="run", help="run name, used in request ids lb-<run>-<iface>-NNNNN")
    p.add_argument("--rate", type=float, default=50.0, help="requests per second (fixed)")
    p.add_argument("--duration", type=float, default=120.0, help="seconds of sending")
    p.add_argument("--devices", type=int, default=20, help="cycle over dev-001 … dev-NNN")
    p.add_argument("--timeout", type=float, default=2.0, help="client timeout / gRPC deadline per request, seconds")
    p.add_argument("--workers", type=int, default=200, help="HTTP thread pool size (caps requests in flight)")
    p.add_argument("--http-url", default="http://localhost:8000")
    p.add_argument("--grpc-target", default="localhost:50051")
    p.add_argument("--seed", action="store_true", help="create the devices (idempotent) and exit")
    p.add_argument("--out", help="also write the JSON summary to this file")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)

    if args.seed:
        seed(args)
        return 0

    results = Results()
    state = {"sent": 0}
    t0 = time.monotonic()

    def schedule(fire) -> None:
        """Start request n at t0 + n/rate until `duration` has passed (open loop)."""
        while True:
            due = t0 + state["sent"] / args.rate
            if due - t0 >= args.duration:
                break
            delay = due - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            fire(state["sent"])
            state["sent"] += 1
        state["send_span_s"] = time.monotonic() - t0

    start_wall = time.time()
    log.info("lb_load %s %s: %g rps for %g s", args.run, args.interface, args.rate, args.duration)
    (run_http if args.interface == "http" else run_grpc)(args, schedule, results, t0)
    end_wall = time.time()

    summary = summarise(args, results, state["sent"], start_wall, end_wall, state["send_span_s"])
    brief = {k: v for k, v in summary.items() if k != "timeline"}
    print(json.dumps(brief))
    if args.out:
        Path(args.out).write_text(json.dumps(summary, indent=1) + "\n")
    return 0 if summary["failures"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
