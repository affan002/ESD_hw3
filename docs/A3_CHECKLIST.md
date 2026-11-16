# A3 requirement checklist

Every bullet of the brief, with where its evidence is. `[x]` means done and verified.

## Part A: replicas and routing
- [ ] Three replicas A, B, C via Docker Compose scaling; each exposes the same HTTP endpoints and gRPC methods
- [ ] Envoy forwards HTTP and gRPC to A only; B and C running but not destinations
- [ ] Replica identifier in logs and metrics; request/trace correlation preserved
- [ ] Business rules and data consistent across replicas; duplicate prevention preserved for writes
- [ ] One HTTP and one unary gRPC operation as test traffic (read-only)
- [ ] Architecture diagram
- [ ] Data, rate-limit counters, idempotency records: shared or per replica; what goes wrong for per-replica state

## Part B: baseline
- [ ] One Grafana dashboard, per replica, HTTP and gRPC: request rate and share of total
- [ ] … active requests
- [ ] … p50/p95 response time (window stated)
- [ ] … errors by status
- [ ] Prometheus scrapes A, B, C and Envoy directly (not through the balanced route)
- [ ] Fixed rate, fixed duration, open loop; rate, sent, successes, failures, client p95 recorded
- [ ] Baseline: A handles all traffic; B and C idle
- [ ] Envoy JSON access logs → Filebeat → Elasticsearch, with selected replica and trace ID
- [ ] Per interface, one search returning the Envoy event together with the replica's event
- [ ] No unique IDs in metric labels

## Part C: load balancing
- [ ] B and C added to Envoy's destinations for HTTP and gRPC, equal-weight round robin
- [ ] Part B workload repeated; each replica ≈ 1/3, shown on the Part B dashboard
- [ ] Shares compared with 33 %; differences from the A-only baseline explained
- [ ] gRPC listener is HTTP/2 (per-call routing), not plain TCP

## Part D: experiments
- [ ] Each experiment starts from Part C, same workload, normal behaviour restored afterwards
- [ ] HTTP and gRPC reported separately; client retries and circuit breakers disabled
- [ ] Prediction, observations, explanation, with charts and client results, for each experiment
- [ ] Weighted round robin 2:1:1: shares vs 50/25/25; latency and active requests explained
- [ ] Least request: fixed delay on B, once round robin and once least request, same delay and workload; B's share, active requests, client p95 compared
- [ ] Failure and recovery: active health checks (interval, timeout, thresholds recorded); stop B during traffic, restart; redistribution and client errors; removal/recovery time related to the settings
- [ ] One summary table of all runs (run/interface, policy and fault, target/actual rps, A/B/C shares, client p95, error rate, explanation)
- [ ] One distribution result checked against Envoy's access logs for the same time range
- [ ] Conclusion 1: strategy for HTTP and for gRPC, from the results
- [ ] Conclusion 2: one workload where the choice would differ
- [ ] Conclusion 3: what happens if Envoy stops; one improvement

## Submit
- [ ] Code/configuration
- [ ] Fixed-rate test client
- [ ] Exported dashboard
- [ ] README: setup, experiment and recovery commands
- [ ] Report: architecture and shared state, health-check settings, metric/query details, baselines, results table, two proxy/backend log examples, conclusions (2–3 pages of explanation)
- [ ] Reused work cited; dependencies and data volumes excluded

## Optional
- [ ] Consistent hashing for one interface; fourth replica; which IDs change destination
