# Design decisions (Assignment 2)

One entry per decision: what was chosen, why, and the alternative that was rejected. The report's reasoning is drawn from here. Decided 2026-10-25.

## Part A: HTTP API

| # | Decision | Choice | Why | Rejected alternative |
|---|---|---|---|---|
| A-1 | User task | **A device submits a reading** (`POST /readings`) | It's the operation that writes data and that a device retries after a timeout, so idempotency, rate limiting and the reliable caller all apply to it naturally | `GET /devices/{id}`: it's safe to repeat already, so there's nothing to protect |
| A-2 | Error format | **RFC 9457 `application/problem+json`** on every error: `{type, title, status, detail, instance, request_id, errors?}` | One shape for 404/409/422/429/500, so a caller branches on `status`/`type` without parsing text. `request_id` links an error to its log lines | Keeping FastAPI's default: `detail` is a string for 404 but a list for 422 |
| A-3 | Idempotency | **Natural key `(device_id, timestamp)`**: an exact repeat returns `200` and the stored reading (`Idempotent-Replayed: true`); the same key with different values returns `409` | A device can't take two readings at the same instant, so the payload already identifies itself; no extra header or key store is needed | An `Idempotency-Key` header: callers must generate and keep a key, and the server must store keys and expire them |
| A-4 | Rate limit | **`POST /readings`, per `device_id`, token bucket: burst 5, refill 1/s.** Over the limit → `429` + `Retry-After` (seconds until the next token) | Behind Docker every caller has the same IP, so per-IP would make the whole fleet share one limit. Per device isolates one misbehaving device. The simulator sends 1 per 5 s per device, far under it | Per client IP (unfair here); global (one device could starve all); fixed window (allows 2× bursts at window edges) |
| A-5 | Caller reliability | **Timeout 2 s · 3 attempts with backoff 0.2/0.4/0.8 s + jitter, only on timeout/connection error/5xx · breaker opens after 3 consecutive failed attempts, half-opens after 10 s** | Normal p99 is about 25 ms, so 2 s only fires on a real problem. Retrying a POST is safe only because of A-3. 4xx is never retried (repeating can't fix it) | Retrying everything (would retry 422s forever); no breaker (keeps hammering a sick server) |
| A-6 | Other endpoints | Validate the `device_id` path param (422 instead of a misleading 404); add `GET /readings/{id}` and a `Location` header on 201; leave `/devices`, `/anomalies` and `/admin/faults` alone | Bad input should be told apart from "not found"; a created resource should be addressable | Renaming `/anomalies` to `/readings?anomalous=true`: would break the dashboard for no real gain |

## Part B: gRPC

| # | Decision | Choice | Why | Rejected alternative |
|---|---|---|---|---|
| B-1 | Operation | Unary `SubmitReading` | It's the chosen task; devices send one reading at a time | Client streaming: no batching need |
| B-2 | Shared rules | Both transports call `app/ingest.py: submit_reading()`; the gRPC server is its own container on the same SQLite volume | Same business rules and same source of truth, not a wrapper around HTTP | gRPC calling the HTTP endpoint (forbidden by the brief, and adds a hop) |
| B-3 | Status mapping | 201/200 → OK · 422 → INVALID_ARGUMENT · 409 → ALREADY_EXISTS · 429 → RESOURCE_EXHAUSTED · 500 → INTERNAL · client timeout → DEADLINE_EXCEEDED | Standard gRPC code meanings | — |
| B-4 | Correlation | `x-request-id` metadata, validated with the same regex as HTTP, set in a server interceptor, echoed back | Same mechanism as A1's `X-Request-ID`, so one ID can be searched across both services | W3C `traceparent`: there's no tracing backend to use it |

## Part C: metrics

| # | Decision | Choice | Why | Rejected alternative |
|---|---|---|---|---|
| C-1 | gRPC metrics | `sensor_grpc_requests_total{method,code}`, `sensor_grpc_request_duration_seconds{method}`, `sensor_grpc_requests_in_progress{method}` | RED metrics for the new service, with bounded labels | — |
| C-2 | Duration measured at | **Server** (interceptor), with the same buckets as the HTTP histogram | Comparable with HTTP, which is also measured server-side; the caller has no metrics endpoint | Client side: includes the network, but the caller isn't scraped |
| C-3 | In-flight vs message size | **In-flight** | The messages are a constant ~100 B, so size carries no signal; in-flight shows a pile-up under delay | Message size |
| C-4 | Other missing metrics | `sensor_rate_limited_total{route}`; outcomes `replayed` and `conflict` and a `transport` label on `sensor_readings_total`; DB write Summary → Histogram | Today you can't see throttling or retries, nor compare HTTP and gRPC outcomes; a Summary can't be aggregated across two processes | None needed (false: these questions are unanswerable today) |
| C-5 | Percentile window | `[1m]` | ≥ 4× the 5 s scrape, matching A1's panels | `[5m]`: too slow for a short demo |

# Design decisions (Assignment 3)

Decided 2026-11-16, after auditing the A3 brief against the A2 code.

## Part A: replicas and routing

| # | Decision | Choice | Why | Rejected alternative |
|---|---|---|---|---|
| A3-1 | Test traffic | **HTTP `GET /devices/{device_id}` and a new unary gRPC `GetDevice(device_id)`**, both read-only and both answered by `db.get_device` + `compute_status()` | A read never trips the per-device rate limit or the SQLite write lock, so per-replica numbers show only the balancer's effect. The two interfaces do the same work, so they can be compared | The A2 writes (`POST /readings`, `SubmitReading`): three processes would queue on one SQLite write lock, and 429s would mix with balancing effects |
| A3-2 | Replica process | **One process per replica**: uvicorn serves HTTP on :8000 and the lifespan starts the gRPC server (:50051) in its own thread pool | One Prometheus registry and one `/metrics` per replica, so one scrape target per replica. HTTP and gRPC share the fault state, so one command delays both interfaces on B | Two processes in one container (two registries, two targets per replica, faults set twice) |
| A3-3 | Replica identity | **Derived from the Compose container name** (`esd_hw3-app-N` → A, B, C, …) by a reverse-DNS lookup of the container's own IP; `REPLICA_ID` overrides it; the hostname is the last resort. Shown as the `replica` field on every log line, the `x-replica` response header / gRPC metadata, and `sensor_replica_info{replica}` | Compose scaling gives every replica identical env vars, so the identity can't be configured per replica; the container name is the only stable per-replica fact. Prometheus also labels each target `replica`, so the two can be cross-checked | Three hand-written services (`app-a`, `app-b`, `app-c`): not "Compose scaling" as the brief asks |
| A3-4 | Shared state | **Data and idempotency records are shared** (one SQLite file on the `sensor-data` volume; the unique index on `(device_id, recorded_at)` rejects duplicates whichever replica gets them). **Rate-limit buckets stay per replica** (in memory) and the consequence is explained and demonstrated | The brief asks to explain what goes wrong; a shared limiter would cost a write transaction on every request | Moving the token bucket into SQLite |
| A3-5 | Correlation | **Trace ID = request ID** (`x-request-id`). Envoy keeps the client's ID (`preserve_external_request_id`) and logs it, plus the chosen replica (`%RESP(X-REPLICA)%`) and `upstream_host` | Same mechanism as A2 B-4, so one Kibana query returns the proxy line and the replica line | W3C `traceparent`: still no tracing backend |
| A3-6 | Envoy ports | Envoy publishes **127.0.0.1:8000 (HTTP), 127.0.0.1:50051 (gRPC) and 127.0.0.1:9901 (admin)**; replicas publish nothing | Existing callers and scripts keep their addresses. Scaled replicas can't share a fixed host port anyway | New ports (8080/8443): every A2 command would change |
| A3-7 | Admin routes | **Envoy answers 403 for `/admin/*`**; faults are set on one replica with `scripts/replica_fault.sh` (`docker exec`) | Through the balancer, `PUT /admin/faults` would fault whichever replica Envoy picked | Routing `/admin` through Envoy |
| A3-8 | Simulator | Moved to Compose profile **`sim`** (sends to `envoy:8000` when started) | The baseline needs B and C to get *no* application traffic, and the runs should see only the workload | Leaving it on (4 rps of background writes in every run) |

## Parts B–D: proxy, metrics and experiments

| # | Decision | Choice | Why | Rejected alternative |
|---|---|---|---|---|
| A3-9 | Phase switching | **Static listeners in `envoy.yaml` + filesystem CDS**: each phase is `monitoring/envoy/clusters/<phase>.yaml`; `scripts/lb_policy.sh <phase>` copies it atomically over `monitoring/envoy/active/cds.yaml` and Envoy reloads it without a restart | Each phase is a small readable file, the diff between phases is exactly the policy change, and Envoy keeps running (no stats reset, no dropped client connection) | A full `envoy-<phase>.yaml` per phase and a restart (about 150 duplicated lines per phase) |
| A3-10 | Endpoints | **STRICT_DNS on `esd_hw3-app-{1,2,3}`**, one cluster per interface (`app_http`, `app_grpc`), explicit `load_balancing_weight` per endpoint. No Envoy retries, no outlier detection | Per-endpoint addresses let the A-only phase list just one replica and the weighted phase give A weight 2. Without retries or outlier detection, active health checks are the only thing that removes a host, so the failure experiment measures them | One endpoint `app` resolving to all three IPs (can't single out A or weight it) |
| A3-11 | gRPC listener | **HTTP connection manager with `codec_type: HTTP2`**, upstream HTTP/2 | Envoy sees each call and routes it separately (the brief's note) | `tcp_proxy`: one routing decision per connection, so one gRPC client pins one replica |
| A3-12 | Health checks | **Active**: HTTP cluster `GET /health`; gRPC cluster the standard `grpc.health.v1` service (`grpcio-health-checking`). Interval 2 s, timeout 1 s, unhealthy threshold 2, healthy threshold 2 | Each interface is checked by its own server, so a dead gRPC server is noticed even if HTTP still answers. Expected removal ≈ 2–4 s, recovery ≈ start-up + 2–4 s | Checking the gRPC cluster over HTTP `/health` (tests uvicorn, not the gRPC server) |
| A3-13 | Workload | **Open loop, fixed 50 rps for 120 s per interface, one interface at a time**; request IDs `lb-<run>-<iface>-NNNNN`; no client retries or breaker; HTTP opens a connection per request, gRPC uses one channel | Fixed offered load makes runs comparable; 120 s gives `[1m]` windows a stable minute in the middle | Closed loop (a slow replica would lower the offered load and hide the effect) |
| A3-14 | Faults | `FaultState` also applies to `GET /devices/{id}` and `GetDevice` (an injected error is 500 / `INTERNAL`) | The least-request experiment needs a fixed delay on B for the test operations on both interfaces | Envoy's fault filter (applies before routing, can't target one upstream) |
| A3-15 | Per-run numbers | `scripts/lb_run_stats.py`: over the run plus margins (5 s before, 15 s after), counts are **summed from raw samples** (handling counter resets and series born mid-run); quantiles use `histogram_quantile(q, sum by (replica, le) (increase(..._bucket[window])))`; in-flight is `avg/max_over_time` over the run. Dashboard panels use `[1m]` | `increase()` extrapolates to the window edges (a 1000-request run read as 1144 over a 40 s window). Runs are separated by idle time, so the margins catch nothing else. Panel values read at an arbitrary moment depend on when you look | `increase(...[run − 15 s])` at the run's end (A1 E1, whose stages were back to back) |
| A3-16 | Envoy workers | **`--concurrency 2`**, fixed | Envoy keeps a separate round-robin position (and active-request counts for least request) in each worker thread, and a new connection lands on one worker. A fixed number makes runs reproducible on any machine; the per-worker effect is explained in the report (HTTP opens a connection per request, so its picks interleave two RR sequences) | Default (one worker per CPU thread: results would depend on the machine) |
