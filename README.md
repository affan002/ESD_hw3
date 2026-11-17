# IoT Sensor Ingestion Service

> **Assignment 3** (load balancing and observability) extends Assignment 2 (tag `a2-submission`), which extended Assignment 1 (tag `a1-submission`).
> - **Three replicas** of the service (A, B, C, by Compose scaling) run behind **Envoy**, each serving HTTP and gRPC.
> - A **per-replica Grafana dashboard**, Envoy's JSON access logs in Elasticsearch, a **fixed-rate test client**, and scripts for every experiment: A only, round robin, weighted 2:1:1, least request with a slow replica, and failure/recovery with active health checks.
> - Results and explanations: [REPORT.md](REPORT.md). Design decisions: [docs/DECISIONS.md](docs/DECISIONS.md). Stage evidence: [docs/BUILD_LOG.md](docs/BUILD_LOG.md).
> - The A1 and A2 demos (simulator walkthrough, E1/E2 experiments, HTTP caller demo) are documented in the README at their tags: `git show a2-submission:README.md`.

## Prerequisites

- Docker Engine with the Compose v2 plugin (`docker compose version`). Your user must be able to use Docker without `sudo`.
- Python 3.12 for the test client, the experiment scripts and the tests (`.venv`, below). `curl`; `jq` is optional.
- Free ports, **all bound to 127.0.0.1** (nothing has a login): **8000** (Envoy → HTTP), **50051** (Envoy → gRPC), **50052** (Envoy plain-TCP gRPC demo), **9901** (Envoy admin), **9090** (Prometheus), **3000** (Grafana), **9200** (Elasticsearch), **5601** (Kibana).
- About **3.5 GB of free RAM** for everything; Kibana alone uses about 1.7 GB. Stop it during measured runs (`docker compose stop kibana`); logs keep flowing into Elasticsearch without it.
- About 7 GB of disk for the images. Elasticsearch's disk watermarks are raised to 93/95/97 % (decision A3-17), but it still stops writing logs when the host disk is 95 % full.

## Start

```bash
python3.12 -m venv .venv
```

```bash
.venv/bin/pip install -r requirements-dev.txt
```

Build the image and start the stack:
- three app replicas;
- Envoy;
- Prometheus, Node Exporter and Grafana;
- Elasticsearch, Kibana, Filebeat, and the one-shot `logs-setup` job.

The simulator is **not** started (profile `sim`, see below).

```bash
docker compose up -d --build
```

```bash
docker compose ps
```

`esd_hw3-app-1`, `-2` and `-3` are `(healthy)`, `logs-setup` is `Exited (0)`, and the rest are `Up`. Then create the 20 devices the test traffic reads (safe to repeat):

```bash
.venv/bin/python scripts/lb_load.py --seed
```

| What | Where |
|---|---|
| HTTP API through Envoy (and the operator dashboard) | http://localhost:8000 |
| gRPC through Envoy (HTTP/2) | `localhost:50051` |
| Envoy admin: per-host health and counters | http://localhost:9901/clusters |
| Grafana, the per-replica dashboard | http://localhost:3000/d/lb-replicas |
| Prometheus targets | http://localhost:9090/targets |
| Kibana | http://localhost:5601 |

The replicas publish no ports; reach one directly with `docker exec esd_hw3-app-2 …`. Through Envoy, `/admin/*` returns 403 (decision A3-7), so the dashboard's fault panel doesn't work there: use `scripts/replica_fault.sh`.

## Part A: replicas and routing

Show or switch Envoy's phase (`a-only`, `round-robin`, `weighted`, `least-request`, `consistent-hash`, `consistent-hash-4`). Envoy reloads its clusters in about a second, without a restart. `round-robin` is the normal state:

```bash
scripts/lb_policy.sh
```

```bash
scripts/lb_policy.sh a-only
```

Each response names the replica that answered, and the request id is kept end to end:

```bash
curl -si -H 'X-Request-ID: demo-1' localhost:8000/devices/dev-001 | grep -iE '^HTTP|x-replica|x-request-id'
```

The same for gRPC: a 5-second, 10-call/s run whose summary shows `replica_counts`:

```bash
.venv/bin/python scripts/lb_load.py --interface grpc --run demo --rate 10 --duration 5
```

Shared vs per-replica state. The per-device rate limit is per replica (7 × 201 under round robin, 5 × 201 + 2 × 429 under A only). Idempotency is shared: a repeat on another replica is a 200 replay.

```bash
scripts/lb_ratelimit_demo.sh
```

## Parts B–D: experiments

Every run is **50 req/s for 120 s per interface, open loop**: the HTTP run, 30 s idle, then the gRPC run. The client has no retries and no circuit breaker. Each run writes `docs/results/a3/<name>_<http|grpc>.json` (client view) and `…_server.json` (per-replica numbers from Prometheus). Request ids are `lb-<run>-<iface>-NNNNN`.

```bash
docker compose stop kibana
```

**B: baseline, A only.** Expected: A gets 100 %.

```bash
scripts/lb_run.sh a-only b_baseline
```

**C: round robin.** Expected: about 33 % each.

```bash
scripts/lb_run.sh round-robin c_round_robin
```

**D1: weighted 2:1:1.** Expected: about 50/25/25.

```bash
scripts/lb_run.sh weighted d_weighted
```

**D2: B delayed by 200 ms, once under round robin and once under least request.** Turn the delay off afterwards.

```bash
scripts/replica_fault.sh B delay 200
```

```bash
scripts/lb_run.sh round-robin d_delay_rr
```

```bash
scripts/lb_run.sh least-request d_delay_lr
```

```bash
scripts/replica_fault.sh B off
```

**D3: failure and recovery** (round robin). Stops B at 30 s and starts it at 70 s, and times removal and recovery from Envoy's `/clusters`. Writes `d_failure_<iface>.json` and `…_events.json`.

```bash
scripts/lb_policy.sh round-robin
```

```bash
.venv/bin/python scripts/lb_failure.py --interface http
```

```bash
.venv/bin/python scripts/lb_failure.py --interface grpc
```

**Results table** for every run → `docs/results/a3/summary.md`:

```bash
python3 scripts/lb_summary.py
```

**Check a run against Envoy's access logs.** It counts request ids per replica in Elasticsearch. Run it within an hour of the run, because logs are deleted after about 70 minutes:

```bash
python3 scripts/lb_es_check.py docs/results/a3/d_weighted_http.json
```

**Both log lines for one request**, Envoy's and the replica's:

```bash
python3 scripts/lb_log_pair.py lb-weighted-http-00042
```

**Optional: plain TCP vs HTTP/2 for gRPC.** Port 50052 is a `tcp_proxy` listener on the same cluster, so each client connection sticks to one replica:

```bash
.venv/bin/python scripts/lb_load.py --interface grpc --run tcp --grpc-target localhost:50052 --duration 10
```

**Optional: consistent hashing and a fourth replica.** One script does it all:
- HTTP ring hash on the path, with 100 devices;
- adds replica D (`--scale app=4`) and lists which devices move;
- removes D, and restores round robin.

Result: `docs/results/a3/e_consistent_hash.txt`.

```bash
.venv/bin/python scripts/lb_hash_demo.py
```

### Recovery: back to normal after any experiment

```bash
scripts/lb_policy.sh round-robin
```

```bash
scripts/replica_fault.sh B off
```

```bash
docker start esd_hw3-app-2
```

If Envoy itself misbehaves, restart it. Its stats reset; its phase comes from `monitoring/envoy/active/cds.yaml`:

```bash
docker compose restart envoy
```

## Observe

- **Grafana → "Load balancing · per replica"** (`lb-replicas`). Per replica and interface: request rate, share of total (dashed 33 % line), active requests (mean by Little's law; the sampled gauge is dotted), p50/p95 over 1 m, and errors by status. Envoy's row shows its per-endpoint requests, the healthy endpoints per cluster, and health-check and connect failures. Set the time range to a run's window from its JSON (`start_utc`, `end_utc`). The older dashboard `sensor-service` sums over all replicas.
- **Prometheus** scrapes `sensor-app` (three targets labelled `replica` A/B/C) and `envoy` directly:
  ```bash
  curl -s localhost:9090/api/v1/targets | jq -r '.data.activeTargets[] | "\(.labels.job) \(.labels.replica // "-") \(.health)"'
  ```
- **Kibana** (start it with `docker compose start kibana`): Discover → data view *Sensor logs*. Saved searches:
  - *Envoy + replica: HTTP request* and *Envoy + replica: gRPC call*: put a request id in the query;
  - *Envoy access log*: `response_flags : "UF"` shows requests sent to a stopped replica.
- **Envoy admin:** `scripts/lb_policy.sh` with no argument prints every host's weight, health flags and active and total requests.

### Simulator (optional)

The 20-device simulator from A1 sends about 4 readings/s through Envoy. It's off by default so that experiments see only the test traffic.

```bash
docker compose --profile sim up -d simulator
```

```bash
docker compose --profile sim stop simulator
```

## Screenshots

Fixed absolute windows, taken from each run's JSON. Grafana panels can be re-captured any time within Prometheus's 7-day retention; Kibana ones only while that run's logs exist:

```bash
scripts/capture_a3_screenshots.sh grafana
```

```bash
scripts/capture_a3_screenshots.sh kibana
```

## Test

The tests use a temporary SQLite file and an in-process gRPC server; Docker isn't needed.

```bash
.venv/bin/pytest -q
```

Validate the Envoy config:

```bash
docker run --rm -v ./monitoring/envoy:/etc/envoy:ro envoyproxy/envoy:v1.40.2 --mode validate -c /etc/envoy/envoy.yaml
```

## Clean up

Stop the containers and keep the data. Volumes: `sensor-data` (readings), `prometheus-data`, `grafana-data`, `es-data`, `filebeat-data`.

```bash
docker compose down
```

Stop and **delete all stored data** (readings, metrics history, logs). This can't be undone:

```bash
docker compose down -v
```

Remove the images:

```bash
docker image rm sensor-api:dev envoyproxy/envoy:v1.40.2 prom/prometheus:v3.15.0 prom/node-exporter:v1.12.1 grafana/grafana:13.2.2 elasticsearch:9.5.3 kibana:9.5.3 elastic/filebeat:9.5.3 curlimages/curl:8.16.0
```
