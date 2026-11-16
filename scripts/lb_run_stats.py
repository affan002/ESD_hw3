#!/usr/bin/env python3
"""Server-side numbers for one lb_load.py run, per replica, from Prometheus.

Reads the run's client summary (interface, start/end), then asks Prometheus, for
exactly that time range, per replica: requests by status, share, server p50/p95,
and requests in flight (mean and max), plus Envoy's own per-endpoint counts. The
window is the run plus margins (5 s before, 15 s after, i.e. three scrapes), so
counters cover the whole run; runs are separated by idle time, so nothing else
falls in it (decision A3-15). Standard library only.

Counts are exact: they are summed from the raw samples (`metric[window]`), not
taken from increase(), which extrapolates to the window edges (1000 requests read
as 1144 over a 40 s window). A counter that drops (a restarted replica) counts
from 0 again, and a series that first appears inside the window (the first
request of a new status code) counts from 0. Quantiles still use
histogram_quantile(increase()), because extrapolation scales every bucket alike.

    python3 scripts/lb_run_stats.py docs/results/a3/b_baseline_http.json
    # writes docs/results/a3/b_baseline_http_server.json and prints it
"""

import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROM = "http://localhost:9090"
BEFORE_S, AFTER_S = 5, 15
SCRAPE_S = 5

SERIES = {
    "http": {
        "requests": 'sensor_http_requests_total{job="sensor-app",route="/devices/{device_id}"}',
        "status_label": "status_code",
        "buckets": 'sensor_http_request_duration_seconds_bucket{job="sensor-app",route="/devices/{device_id}"}',
        "duration_sum": 'sensor_http_request_duration_seconds_sum{job="sensor-app",route="/devices/{device_id}"}',
        "in_flight": 'sensor_http_requests_in_progress{job="sensor-app"}',
        "envoy_cluster": "app_http",
    },
    "grpc": {
        "requests": 'sensor_grpc_requests_total{job="sensor-app",method="GetDevice"}',
        "status_label": "code",
        "buckets": 'sensor_grpc_request_duration_seconds_bucket{job="sensor-app",method="GetDevice"}',
        "duration_sum": 'sensor_grpc_request_duration_seconds_sum{job="sensor-app",method="GetDevice"}',
        "in_flight": 'sensor_grpc_requests_in_progress{job="sensor-app",method="GetDevice"}',
        "envoy_cluster": "app_grpc",
    },
}


def ts(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def query(expr: str, at: float) -> list[dict]:
    url = f"{PROM}/api/v1/query?" + urllib.parse.urlencode({"query": expr, "time": f"{at:.3f}"})
    with urllib.request.urlopen(url, timeout=10) as resp:
        body = json.load(resp)
    if body["status"] != "success":
        raise RuntimeError(f"{expr}: {body}")
    return body["data"]["result"]


def exact_increase(selector: str, at: float, window_s: int, *labels: str) -> dict:
    """Sum of counter increases per label combination, from raw samples in (at - window, at]."""
    out: dict[str, float] = {}
    for series in query(f"{selector}[{window_s}s]", at):
        samples = [float(v) for _, v in series["values"]]
        first_t = float(series["values"][0][0])
        # A series that didn't exist at the window start began at 0 inside it.
        total = samples[0] if first_t > at - window_s + 1.5 * SCRAPE_S else 0.0
        for prev, cur in zip(samples, samples[1:]):
            total += cur - prev if cur >= prev else cur  # a drop = process restart: counts from 0
        key = "/".join(series["metric"].get(lbl, "-") for lbl in labels)
        out[key] = out.get(key, 0.0) + total
    return out


def by(results: list[dict], *labels: str) -> dict:
    out = {}
    for r in results:
        key = "/".join(r["metric"].get(lbl, "-") for lbl in labels)
        value = float(r["value"][1])
        out[key] = None if value != value else value  # NaN (no data) -> None
    return out


def main() -> int:
    client_path = Path(sys.argv[1])
    client = json.loads(client_path.read_text())
    s = SERIES[client["interface"]]
    start, end = ts(client["start_utc"]), ts(client["end_utc"])
    at = end + AFTER_S
    wait = at + 1 - time.time()
    if wait > 0:
        time.sleep(wait)  # let Prometheus scrape the end of the run
    window_s = int(round(at - (start - BEFORE_S)))
    w = f"{window_s}s"
    run_w = f"{max(int(end - start), 10)}s"  # in-flight: only while the load was on

    counts = exact_increase(s["requests"], at, window_s, "replica", s["status_label"])
    per_replica: dict[str, dict] = {}
    for key, value in counts.items():
        replica, status = key.split("/")
        entry = per_replica.setdefault(replica, {"requests": 0, "by_status": {}})
        n = round(value or 0)
        if n:
            entry["by_status"][status] = n
            entry["requests"] += n
    total = sum(e["requests"] for e in per_replica.values())
    for q in ("0.5", "0.95"):
        values = by(query(f'histogram_quantile({q}, sum by (replica, le) (increase({s["buckets"]}[{w}])))', at), "replica")
        for replica, v in values.items():
            per_replica.setdefault(replica, {"requests": 0, "by_status": {}})[f"server_p{int(float(q) * 100)}_ms"] = (
                round(v * 1000, 1) if v is not None else None)
    # Mean requests in flight by Little's law: seconds spent serving test requests / seconds
    # of run. Exact, unlike the in-progress gauge, which is only sampled every 5 s and (for
    # HTTP) also counts the /metrics scrape that reads it, so it never shows below 1.
    busy = exact_increase(s["duration_sum"], at, window_s, "replica")
    for replica, seconds in busy.items():
        per_replica.setdefault(replica, {"requests": 0, "by_status": {}})["in_flight_mean"] = round(seconds / (end - start), 3)
    for fn in ("avg", "max"):
        values = by(query(f'{fn}_over_time({s["in_flight"]}[{run_w}])', end), "replica")
        for replica, v in values.items():
            per_replica.setdefault(replica, {"requests": 0, "by_status": {}})[f"in_flight_gauge_{fn}"] = round(v or 0, 2)
    for entry in per_replica.values():
        entry["share_pct"] = round(100 * entry["requests"] / total, 1) if total else 0.0

    cluster = s["envoy_cluster"]
    envoy = {}
    for metric in ("rq_total", "rq_error", "rq_timeout", "cx_connect_fail"):
        values = exact_increase(f'envoy_cluster_endpoint_{metric}{{envoy_cluster_name="{cluster}"}}',
                                at, window_s, "envoy_endpoint_hostname")
        for host, v in values.items():
            envoy.setdefault(host, {})[metric] = round(v or 0)
    for host, v in by(query(f'max by (envoy_endpoint_hostname) (max_over_time(envoy_cluster_endpoint_rq_active'
                            f'{{envoy_cluster_name="{cluster}"}}[{run_w}]))', end), "envoy_endpoint_hostname").items():
        envoy.setdefault(host, {})["rq_active_max"] = round(v or 0)

    out = {
        "run": client["run"], "interface": client["interface"],
        "window": {"from_utc": client["start_utc"], "to_utc": client["end_utc"],
                   "query_range": w, "evaluated_at": datetime.fromtimestamp(at, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")},
        "total_requests": total,
        "replicas": dict(sorted(per_replica.items())),
        "envoy_endpoints": dict(sorted(envoy.items())),
    }
    text = json.dumps(out, indent=1)
    print(text)
    client_path.with_name(client_path.stem + "_server.json").write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
