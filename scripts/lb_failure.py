#!/usr/bin/env python3
"""Failure and recovery experiment (Part D3): stop replica B during traffic, then start it.

Runs scripts/lb_load.py for one interface (50 req/s, 120 s, round robin), and meanwhile:
  t = STOP_AT  s: `docker stop esd_hw3-app-2`   (B gets SIGTERM and exits)
  t = START_AT s: `docker start esd_hw3-app-2`
Throughout, polls Envoy's admin /clusters every 250 ms and records every change of
B's health flags and address in the interface's cluster, so removal and recovery
can be timed against the health-check settings (interval 2 s, timeout 1 s,
unhealthy 2, healthy 2). Also records when B's own /health answers again.

Writes docs/results/a3/<name>_<iface>.json (lb_load summary with per-second timeline)
and <name>_<iface>_events.json (stop/start times, health transitions, derived
durations). Needs the .venv (lb_load uses grpcio).

    .venv/bin/python scripts/lb_failure.py --interface http
    .venv/bin/python scripts/lb_failure.py --interface grpc
"""

import argparse
import json
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADMIN = "http://localhost:9901/clusters"
CONTAINER = "esd_hw3-app-2"
HOST = "B"


def utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def b_state(cluster: str) -> list[tuple[str, str]]:
    """[(address, health_flags)] of host B in `cluster` ([] if Envoy has no such host)."""
    with urllib.request.urlopen(ADMIN, timeout=1) as resp:
        lines = resp.read().decode().splitlines()
    hosts: dict[str, dict] = {}
    for line in lines:
        parts = line.split("::")
        if len(parts) == 4 and parts[0] == cluster:
            hosts.setdefault(parts[1], {})[parts[2]] = parts[3]
    return sorted((addr, h.get("health_flags", "?")) for addr, h in hosts.items() if h.get("hostname") == HOST)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--interface", choices=("http", "grpc"), required=True)
    p.add_argument("--name", default="d_failure")
    p.add_argument("--rate", type=float, default=50.0)
    p.add_argument("--duration", type=float, default=120.0)
    p.add_argument("--stop-at", type=float, default=30.0)
    p.add_argument("--start-at", type=float, default=70.0)
    p.add_argument("--out-dir", default=str(ROOT / "docs" / "results" / "a3"),
                   help="where to write the results (use another directory for practice runs)")
    args = p.parse_args()
    cluster = "app_http" if args.interface == "http" else "app_grpc"
    Path(args.out_dir).mkdir(parents=True, exist_ok=True)
    out = Path(args.out_dir) / f"{args.name}_{args.interface}.json"

    events: list[dict] = []
    stop_polling = threading.Event()

    def poll() -> None:
        last = None
        while not stop_polling.is_set():
            now = time.time()
            try:
                state = b_state(cluster)
            except Exception as exc:  # admin briefly unavailable
                state = [("admin-error", type(exc).__name__)]
            if state != last:
                events.append({"t_utc": utc(now), "t": now, "kind": "envoy_b_state", "state": state})
                last = state
            time.sleep(0.25)

    def b_health_ok() -> bool:
        r = subprocess.run(["docker", "exec", CONTAINER, "python", "-c",
                            "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=1)"],
                           capture_output=True)
        return r.returncode == 0

    poller = threading.Thread(target=poll, daemon=True)
    poller.start()
    load = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "lb_load.py"), "--interface", args.interface,
                             "--run", "failure", "--rate", str(args.rate), "--duration", str(args.duration),
                             "--out", str(out)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0 = time.time()

    def at(offset: float) -> None:
        time.sleep(max(0.0, t0 + offset - time.time()))

    at(args.stop_at)
    events.append({"t_utc": utc(time.time()), "t": time.time(), "kind": "docker_stop_issued"})
    subprocess.run(["docker", "stop", CONTAINER], check=True, capture_output=True)
    events.append({"t_utc": utc(time.time()), "t": time.time(), "kind": "docker_stop_returned"})

    at(args.start_at)
    events.append({"t_utc": utc(time.time()), "t": time.time(), "kind": "docker_start_issued"})
    subprocess.run(["docker", "start", CONTAINER], check=True, capture_output=True)
    events.append({"t_utc": utc(time.time()), "t": time.time(), "kind": "docker_start_returned"})
    while not b_health_ok():
        time.sleep(0.1)
    events.append({"t_utc": utc(time.time()), "t": time.time(), "kind": "b_health_endpoint_ok"})

    load.wait()
    time.sleep(2)
    stop_polling.set()
    poller.join()

    events.sort(key=lambda e: e["t"])
    for e in events:
        e["t_rel_s"] = round(e["t"] - t0, 2)

    def first(kind: str, pred=lambda e: True, after: float = 0.0):
        return next((e for e in events if e["kind"] == kind and e["t"] >= after and pred(e)), None)

    stop = first("docker_stop_issued")
    start = first("docker_start_issued")
    unhealthy = first("envoy_b_state", lambda e: any("failed_active_hc" in f for _, f in e["state"]) or not e["state"],
                      after=stop["t"])
    healthy_again = first("envoy_b_state", lambda e: e["state"] and all(f == "healthy" for _, f in e["state"]),
                          after=start["t"])
    b_up = first("b_health_endpoint_ok")
    summary = {
        "interface": args.interface, "cluster": cluster, "container": CONTAINER,
        "health_check": {"interval_s": 2, "timeout_s": 1, "unhealthy_threshold": 2, "healthy_threshold": 2,
                         "no_traffic_interval_s": 2, "dns_refresh_rate_s": 5},
        "removal_s_after_stop_issued": round(unhealthy["t"] - stop["t"], 2) if unhealthy else None,
        "docker_stop_took_s": round(first("docker_stop_returned")["t"] - stop["t"], 2),
        "recovery_s_after_start_issued": round(healthy_again["t"] - start["t"], 2) if healthy_again else None,
        "b_health_ok_s_after_start_issued": round(b_up["t"] - start["t"], 2) if b_up else None,
        "events": [{k: v for k, v in e.items() if k != "t"} for e in events],
    }
    events_path = out.with_name(out.stem + "_events.json")
    events_path.write_text(json.dumps(summary, indent=1) + "\n")
    client = json.loads(out.read_text())
    print(json.dumps({k: v for k, v in summary.items() if k != "events"}))
    print(json.dumps({k: client[k] for k in ("sent", "successes", "failures", "status_counts", "replica_counts")}))
    for e in summary["events"]:
        print(f"  t={e['t_rel_s']:7.2f}s {e['t_utc']} {e['kind']} {e.get('state', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
