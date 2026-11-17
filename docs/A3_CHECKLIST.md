# A3 requirement checklist

Every bullet of the brief, with where its evidence is. `[x]` means done and verified.

## Part A: replicas and routing
- [x] Three replicas A, B, C via Docker Compose scaling; each exposes the same HTTP endpoints and gRPC methods (`docker-compose.yml` `deploy.replicas: 3`; report A.1)
- [x] Envoy forwards HTTP and gRPC to A only; B and C running but not destinations (`clusters/a-only.yaml`; BUILD_LOG A3-2)
- [x] Replica identifier in logs and metrics; request/trace correlation preserved (report A.2; `app/replica.py`)
- [x] Business rules and data consistent across replicas; duplicate prevention preserved for writes (report A.3; `a_idempotency_cross_replica.txt`; `tests/test_idempotency.py`)
- [x] One HTTP and one unary gRPC operation as test traffic, read-only (report A.4; decision A3-1)
- [x] Architecture diagram (`docs/diagrams/architecture.png`; report A.1)
- [x] Data, rate-limit counters, idempotency records: shared or per replica; what goes wrong for per-replica state (report A.3; `a_rate_limit_per_replica.txt`)

## Part B: baseline
- [x] One Grafana dashboard, per replica, HTTP and gRPC: request rate and share of total (`lb-replicas.json`; report B.1)
- [x] … active requests (Little's law plus the sampled gauge)
- [x] … p50/p95 response time, window stated (1 m)
- [x] … errors by status (replica and Envoy-only)
- [x] Prometheus scrapes A, B, C and Envoy directly (jobs `sensor-app`, `envoy`; `b_prometheus_targets.png`)
- [x] Fixed rate, fixed duration, open loop; rate, sent, successes, failures, client p95 recorded (`scripts/lb_load.py`; report B.2)
- [x] Baseline: A handles all traffic; B and C idle (report B.2; `b_baseline_*`)
- [x] Envoy JSON access logs → Filebeat → Elasticsearch, with selected replica and trace ID (`envoy.yaml`, `filebeat.yml`)
- [x] Per interface, one search returning the Envoy event together with the replica's event (report B.3; `b_kibana_*_envoy_and_replica.png`)
- [x] No unique IDs in metric labels (report B.1)

## Part C: load balancing
- [x] B and C added to Envoy's destinations for HTTP and gRPC, equal-weight round robin (`clusters/round-robin.yaml`)
- [x] Part B workload repeated; each replica ≈ 1/3, shown on the Part B dashboard (report C; `c_round_robin_*`)
- [x] Shares compared with 33 %; differences from the A-only baseline explained (report C)
- [x] gRPC listener is HTTP/2 (per-call routing), not plain TCP; tcp_proxy comparison (report C; `c_grpc_tcp_vs_http2.txt`)

## Part D: experiments
- [x] Each experiment starts from Part C, same workload, normal behaviour restored afterwards (report D.1; README *Recovery*)
- [x] HTTP and gRPC reported separately; client retries and circuit breakers disabled (report D.1–D.5)
- [x] Prediction, observations, explanation, with charts and client results, for each experiment (`d_predictions.md`; report D.2–D.4)
- [x] Weighted round robin 2:1:1: shares vs 50/25/25; latency and active requests explained (report D.2)
- [x] Least request: fixed delay on B, once round robin and once least request, same delay and workload; B's share, active requests, client p95 compared (report D.3)
- [x] Failure and recovery: active health checks (interval, timeout, thresholds recorded); stop B during traffic, restart; redistribution and client errors; removal/recovery time related to the settings (report D.1, D.4; `d_failure_*_events.json`)
- [x] One summary table of all runs with the brief's columns (report D.5; `summary.md`)
- [x] One distribution result checked against Envoy's access logs for the same time range (report D.6; `d_weighted_http_access_log.json`)
- [x] Conclusion 1: strategy for HTTP and for gRPC, from the results
- [x] Conclusion 2: one workload where the choice would differ
- [x] Conclusion 3: what happens if Envoy stops; one improvement

## Submit
- [x] Code/configuration
- [x] Fixed-rate test client (`scripts/lb_load.py`, `lb_run.sh`, `lb_run_stats.py`, `lb_failure.py`)
- [x] Exported dashboard (`monitoring/grafana/dashboards/lb-replicas.json`)
- [x] README: setup, experiment and recovery commands (walked through on the running stack: BUILD_LOG A3-8)
- [x] Report: architecture and shared state, health-check settings, metric/query details, baselines, results table, two proxy/backend log examples, conclusions (≈ 1,700 words including captions)
- [x] Reused work cited (report *Credits*); dependencies and data volumes excluded (`.gitignore`: `.venv/`, `data/`; volumes are not in the repo)

## Optional
- [ ] Consistent hashing for one interface; fourth replica; which IDs change destination (not done)
