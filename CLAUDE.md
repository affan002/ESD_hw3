# CLAUDE.md

Conventions for this repo. Keep them consistent, and update this file only when a new convention is actually decided.

## Project
- **Assignment 3 (Load balancing and observability)** is the current work. This repo is cloned from `ESD_hw2`, which is frozen at tag `a2-submission`.
  - A3 parts:
    - A: three replicas A/B/C via Compose scaling behind Envoy, which forwards to A only.
    - B: a per-replica Grafana dashboard, a fixed-rate baseline, and Envoy access logs in Elasticsearch.
    - C: equal round robin.
    - D: experiments (weighted 2:1:1, least request vs round robin with B delayed, failure/recovery with active health checks), a results table and conclusions.
  - Decisions A3-1…A3-17 are in `docs/DECISIONS.md`.
  - The brief-to-evidence checklist is `docs/A3_CHECKLIST.md`.
  - The A2 sections below still apply unless an A3 decision changes them.
- IoT sensor ingestion API, plus a device simulator. **Assignment 2 (HTTP APIs, gRPC, metrics)** extends the Assignment 1 observability project, which is frozen at tag `a1-submission` (repo `affan002/ESD_hw1`).
- A2 parts: A (improve the HTTP API: error format, OpenAPI, idempotency, rate limiting, a Python caller with timeout/retry/circuit breaker), B (a gRPC service for one operation, sharing the business rules and database), C (extend the metrics for gRPC in Prometheus/Grafana).
- A1 sections below describe what already exists; everything in them still applies unless an A2 decision changes it.
- **Design decisions are the author's.** Each one is recorded in `docs/DECISIONS.md` (choice, reason, rejected alternative). Don't make a design choice that isn't recorded there; ask.
- Living docs, updated stage by stage after each stage's done check passes:
  - `REPORT.md`: **concise**, answering exactly what each part of the assignment asks, in short paragraphs and tables. The style is `A. Project`, `B.1 …` numbered subsections, and conclusions with the key numbers only. Aim for about 50–100 lines per part. No stage-by-stage logs.
  - There is no walkthrough in the repo: the author's learning guide is private and not submitted. Don't link to it or recreate it.
  - Screenshots go in `docs/screenshots/`, named with a part prefix (`a_…`, `b_…`, `c_…`, `d_…`). Kibana screenshots use Discover URLs with `_g=(time:(from:'…Z',to:'…Z'))` and `_a=(dataSource:(dataViewId:sensor-logs,type:dataView),query:(language:kuery,query:'…'))`, captured with headless Chrome using `--virtual-time-budget=40000`. If the file size doesn't change after a capture, it failed (Kibana wasn't ready). Capture them with `scripts/capture_screenshots.sh`, which uses headless Chrome, a fixed absolute time window (never `now`, because virtual time makes graphs dip at the right edge) and the light theme. Every screenshot in the report needs an interpretation of what it actually shows, not just a caption.
  - **The architecture diagram is text:** `docs/diagrams/architecture.mmd` (Mermaid, `flowchart LR`), rendered to `.svg`/`.png` by `scripts/render_diagram.sh` (headless Chrome with Mermaid 11 from jsDelivr, PNG at 2×). **Keep it simple:** one subgraph per concern (Clients, Load balancer, replicas, Metrics, Logs), one or two short lines per node, and a short label per arrow. Failure behaviour and storage detail go in REPORT D.1–D.2 tables, not in the diagram. **When `docker-compose.yml` or a `monitoring/` config changes a service, port or connection, update the `.mmd` and re-render.**
  - `docs/BUILD_LOG.md`: the stage-by-stage evidence (what was built, the exact command, the trimmed real output). Append a section per stage here; the report links to it.
  - `README.md`: start/use/test/clean up. It must always match what runs right now.
  - `CLAUDE.md`: this file.

## Stack (pinned in requirements*.txt)
- Python 3.12, FastAPI 0.141.1, uvicorn[standard] 0.54.0, pydantic 2.13.5, httpx 0.28.1, pytest 9.1.1.
- Stdlib `sqlite3` (no ORM) and stdlib `logging`.
- One uvicorn worker only. Don't add `--workers`: `prometheus_client` keeps metrics in process memory, so extra workers would each expose partial numbers.
- Route handlers are sync `def`, so they run in the threadpool.

## Layout
- `app/`: the API.
  - `main.py`: `create_app()` factory and routes.
  - `config.py`: `load_settings()`, reads env vars.
  - `__main__.py`: `python -m app` (one replica: HTTP plus in-process gRPC).
  - `grpc_server.py`: the gRPC servicer, interceptor and health service.
  - `replica.py`: the replica name (A/B/C) from the Compose container name.
  - `metrics.py`: every Prometheus metric, plus the device-status collector.
  - `static/`: the operator dashboard.
- `monitoring/`: Envoy config (`envoy/`), Prometheus config (`prometheus/prometheus.yml`), Grafana provisioning and dashboards (`grafana/`), `filebeat/filebeat.yml`, `elasticsearch/` (index template, ILM policy), `kibana/saved-objects.ndjson`, and `logs-setup.sh`.
- `docs/`: `BUILD_LOG.md` (evidence), `diagrams/` (the architecture as Mermaid, plus its renders) and `screenshots/`.
- `scripts/`: `capture_screenshots.sh`, `render_diagram.sh`, the A1 Part E scripts (`load.py`, `e1_stage_stats.py`, `cardinality_demo.py`, `cardinality_demo.sh`), and the A3 scripts: `lb_policy.sh`, `lb_run.sh`, `lb_load.py`, `lb_run_stats.py`, `lb_failure.py`, `lb_es_check.py`, `lb_log_pair.py`, `lb_summary.py`, `lb_ratelimit_demo.sh`, `replica_fault.sh` and `capture_a3_screenshots.sh`.
- `docs/results/`: raw experiment outputs (JSON), plus `e1_predictions.md`; A3's are in `docs/results/a3/` (with `d_predictions.md`). Predictions are written **before** a run and never edited afterwards.
- `simulator/`: the device fleet simulator (`python -m simulator.simulate`).
- `tests/`: pytest. The `client` fixture in `conftest.py` builds the app against a temporary `DB_PATH`.

## Run and test
- Tests: `.venv/bin/pytest -q`
- API locally: `.venv/bin/python -m app` (port 8000)
- Docker: `docker compose up -d --build` (the full stack: 3 app replicas, envoy, prometheus, node-exporter, grafana, elasticsearch, kibana, filebeat, and the one-shot logs-setup; the simulator only with `--profile sim`) · replicas + proxy only: `docker compose up -d --build app envoy` · stop: `docker compose down` · wipe all data, including metrics history: `docker compose down -v`

## Docker and ports
- **Every published port is bound to 127.0.0.1** (`"127.0.0.1:8000:8000"`, and the same for 50051, 50052, 9901, 3000, 9090, 9200 and 5601). Nothing in the stack has authentication (`/admin/faults`, Grafana anonymous, Elasticsearch security off), so never publish on 0.0.0.0. Node Exporter (host network) uses `--web.listen-address=172.17.0.1:9100`, Docker's bridge address, which is what `host.docker.internal` resolves to for Prometheus.
- **A3:** Compose service `app` → image `sensor-api:dev`, **`deploy.replicas: 3`** (containers `esd_hw3-app-1…3` = replicas A, B, C).
  - No published ports. Each replica is one process: HTTP :8000 plus gRPC :50051, started from the lifespan.
  - Reach one replica with `docker exec esd_hw3-app-N …`.
  - The old `api` and `grpc` services are gone.
- Compose service `envoy` (`envoyproxy/envoy:v1.40.2`, `--concurrency 2`, decision A3-16) publishes:
  - **127.0.0.1:8000** (HTTP) and **127.0.0.1:50051** (gRPC, HTTP/2 connection manager);
  - **127.0.0.1:50052** (`tcp_proxy` demonstration only);
  - **127.0.0.1:9901** (admin).
- Envoy config lives in `monitoring/envoy/`:
  - `envoy.yaml`: static listeners, JSON access log, `/admin/*` → 403.
  - `clusters/<phase>.yaml`: `a-only | round-robin | weighted | least-request`. Every phase has the same clusters `app_http` and `app_grpc`, strict DNS on `esd_hw3-app-N` with endpoint `hostname` A/B/C, and the same active health checks (2 s / 1 s / 2 / 2).
  - `active/cds.yaml`: watched by Envoy. Committed as round-robin, the normal state; `scripts/lb_policy.sh <phase>` swaps it atomically.
  - No Envoy retries or outlier detection (A3-10).
- Compose service `simulator` reuses the image and is in **profile `sim`** (`docker compose --profile sim up -d simulator`), targeting `envoy:8000`.
- Compose service `prometheus` (`prom/prometheus:v3.15.0`) on port **9090**, with volume `prometheus-data` and 7-day retention.
  - Config: `monitoring/prometheus/prometheus.yml`, mounted read-only. It reloads with `curl -XPOST localhost:9090/-/reload` because `--web.enable-lifecycle` is on.
  - Scrape interval **5 s**. Jobs:
    - `sensor-app` → `esd_hw3-app-{1,2,3}:8000` with target label `replica` A/B/C: each replica directly, never through Envoy;
    - `envoy` → `envoy:9901/stats/prometheus`;
    - `prometheus` → `localhost:9090`.
  - Job `node` → `host.docker.internal:9100`. This needs `extra_hosts: host-gateway` on `prometheus`.
  - All monitoring config lives under `monitoring/`.
- Compose service `node-exporter` (`prom/node-exporter:v1.12.1`) uses `network_mode: host`, `pid: host` and `/:/host:ro,rslave` with `--path.rootfs=/host`. It listens on **172.17.0.1:9100** only (not the LAN), and has no `ports:` entry because it's on the host network. From the host, use `curl 172.17.0.1:9100/metrics`. Don't move it onto the Compose network, or it will measure a container instead of the host.
- Never display `node_dmi_info`'s `chassis_asset_tag`, or MAC addresses, in dashboards or the report.
- Compose service `grafana` (`grafana/grafana:13.2.2`) on port **3000**, with volume `grafana-data`. Anonymous Viewer access is on; admin login is admin/admin.
  - **Everything is provisioned from files and nothing is clicked together in the UI.** The datasource is in `monitoring/grafana/provisioning/datasources/prometheus.yml` (uid **`prometheus`**; every panel references it by this uid). The dashboard provider is `provisioning/dashboards/dashboards.yml` (folder "Sensor Ingestion", `allowUiUpdates: false`).
  - Dashboards are JSON in `monitoring/grafana/dashboards/`: `sensor-service.json` (uid `sensor-service`, also the home dashboard; sums over all replicas), `node-host.json` (uid `node-host`) and, for A3, **`lb-replicas.json`** (uid `lb-replicas`: per-replica rate, share, active (Little's law), p50/p95, errors, plus Envoy's view; `timezone: utc`; panel ids are used by `scripts/capture_a3_screenshots.sh`). Edit the JSON; Grafana reloads it within 30 s.
  - The empty `provisioning/alerting/` and `provisioning/plugins/` directories exist only to stop Grafana logging errors.
  - PromQL conventions in panels: fixed `[1m]` rate windows, which is ≥ 4× the 5 s scrape; percentiles use `histogram_quantile(q, sum by (le) (rate(..._bucket[1m])))`; every panel has a `description`.
- Named volume `sensor-data` is mounted at `/data`. The container runs as non-root user `app` (uid 10001).
- The healthcheck uses a Python urllib call to `/health`, because the slim image has no curl.

## Config (env vars)
`DB_PATH` (default `./data/sensors.db`, `/data/sensors.db` in the container), `LOG_LEVEL` (INFO), `API_HOST` (0.0.0.0), `API_PORT` (8000), `STALE_AFTER_S` (30), `FUTURE_SKEW_S` (60), `RATE_LIMIT_BURST` (5), `RATE_LIMIT_PER_S` (1), `GRPC_PORT` (50051), `GRPC_METRICS_PORT` (9101, standalone `python -m app.grpc_server` only), `GRPC_ENABLED` (`python -m app` sets true; `create_app()` alone defaults false), `REPLICA_ID` (empty = derive from the container name, `app/replica.py`; tests set `T` in `conftest.py`).

## A2 conventions (decided in docs/DECISIONS.md)
- **Errors:** every error is RFC 9457 `application/problem+json` `{type, title, status, detail, instance, request_id, errors?}` (`app/problems.py`). `type` is `urn:sensor-api:problem:<slug>`. Raise `ProblemError(status, slug, detail, headers)` from routes; 422s go through the `RequestValidationError` handler and carry `errors: [{field, type, message}]`.
- **Shared business rule:** `app/ingest.py: submit_reading()` is the only place that classifies and stores a reading; HTTP (`app/main.py`) and gRPC (`app/grpc_server.py`) both call it with `transport="http"|"grpc"`. Transport concerns (parsing, rate limit, faults, status mapping) stay in the callers.
- **Idempotency:** `POST /readings` / `SubmitReading` are idempotent on `(device_id, recorded_at)` (unique index `ux_readings_device_recorded`): identical repeat → 200 / `replayed=true`; different values → 409 / `ALREADY_EXISTS`.
- **Rate limit:** `app/ratelimit.py` token bucket per `device_id`, `RATE_LIMIT_BURST` (5) and `RATE_LIMIT_PER_S` (1); 429 + `Retry-After` (gRPC: `RESOURCE_EXHAUSTED` + `retry-after` trailing metadata). In-memory, per process. Tests set `RATE_LIMIT_BURST=1000` in `conftest.py` except `tests/test_rate_limit.py`.
- **OpenAPI:** `docs/openapi.json` is generated by `scripts/export_openapi.py`; re-run it after any route change (`tests/test_openapi.py` enforces it). Declare error responses with `responses={code: {"model": Problem, …}}`; `_problem_openapi()` rewrites them to `application/problem+json`.
- **gRPC:** `proto/sensor_ingest.proto` (package `sensor.v1`: `SubmitReading`, and since A3 the read-only `GetDevice`), generated into `app/grpc_gen/` by `scripts/gen_proto.sh` (committed; never edit by hand). Since A3 the gRPC server runs inside each replica's process (`grpc_server.serve()` from the lifespan, sharing the registry and `FaultState`) and registers `grpc.health.v1` for Envoy's health checks. The interceptor skips health calls. `ObservabilityInterceptor` handles `x-request-id` metadata, the `grpc_request` log line and the `sensor_grpc_*` metrics. Log `service` is `sensor-grpc`.
- **Callers:** `clients/http_caller.py` (timeout 2 s, 3 attempts, backoff, breaker 3 failures / 10 s) and `clients/grpc_caller.py` (deadline 2 s). Request IDs `cli-<12 hex>`; demo scripts use `ex-`, `rl-`, `b-…`, `trace-…`, `traffic-…`.
- **Screenshots for A2** go in `docs/screenshots/a2/` (`scripts/capture_a2_screenshots.sh`); raw outputs in `docs/results/` (`a_…`, `b_…`, `c_…`).

## Routes (current)
- `GET /` (dashboard), `/static/*`
- `GET /health`
- `POST /readings` (201 + `Location`; 200 replay; 409; 422; 429)
- `GET /readings/{reading_id}`
- `GET /devices[?status=]`
- `GET /devices/{device_id}`
- `GET /devices/{device_id}/readings[?limit=&anomalies_only=]`
- `GET /anomalies[?limit=]`
- `GET|PUT|DELETE /admin/faults`: the fault-injection contract is `FaultConfig {delay_ms 0..5000, delay_every_n ≥1, error_every_n ≥0 (0=off)}`.
  - `PUT` replaces the whole config and resets the request counter. `DELETE` turns everything off.
  - Faults apply to `POST /readings` (via `faults.apply()` at the top of the handler, after body validation) and, since A3, to `GET /devices/{device_id}` and gRPC `GetDevice` (one shared `FaultState` per replica).
  - The state is in memory (`app.state.faults`), so a restart resets it. It's deterministic (every Nth request), never random.
  - Log events: `fault_config_changed` (WARNING) and `fault_injected` (`fault=delay` at WARNING with `delay_ms`, or `fault=error` at ERROR).
- Lists wrap their items in an object (`{"count","devices"}`, `{"device_id","readings"}`, `{"readings"}`), never a bare array. `limit` is always 1–500 with a default of 50 (the `Limit` alias in `main.py`). `device_id` path params are validated (`DeviceId` alias: malformed → 422). Unknown devices return a 404 problem with `detail: "device not found"`.

## API conventions
- Routes use plural nouns and no verbs: `/readings`, `/devices`, `/anomalies`. Operator controls go under `/admin/*`.
- JSON field names are snake_case with units as suffixes: `temperature_c`, `humidity_pct`, `battery_pct`.
- `device_id` must match `^dev-\d{3}$` (`DEVICE_ID_PATTERN` in `app/models.py`).
- Validation errors use FastAPI's default `422 {"detail":[...]}`. Custom validation failures raise `RequestValidationError` with a custom `type` (e.g. `timestamp_in_future`), so they go through the same handler and log line.
- The validation handler logs `reading_rejected` for `POST /readings` and `request_invalid` for everything else, with `errors=[{field,type}]` only.
- Ingestion log events: `device_registered`, `reading_stored` (INFO) and `anomaly_detected` (WARNING, includes values).

## Data model (SQLite, `app/db.py`)
- All SQL lives in `app/db.py`. Routes call its functions and never write SQL themselves.
- Connections: `db.connect(path)` (autocommit, `busy_timeout=5000`, `row_factory=Row`). WAL is set once in `init_schema()`, which runs in the lifespan.
- Each request gets a connection through the `get_conn` dependency in `main.py`.
- Writes use an explicit `BEGIN IMMEDIATE` … `COMMIT`/`ROLLBACK`.
- `readings`: `id, device_id, recorded_at, received_at, temperature_c, humidity_pct, battery_pct, is_anomaly (0/1), anomaly_reasons (JSON array text)`
- `devices`: `device_id (PK), first_seen, last_seen, reading_count, anomaly_count, last_reading_id`
- `last_seen` and `first_seen` use server time (`received_at`), never the device clock.
- API field names differ from the database in one place: the API's `timestamp` is `readings.recorded_at`.
- Models are in `app/models.py`. Physical limits (a 422 when exceeded) are in `ReadingIn`; the expected range (an anomaly) is in `app/anomaly.py`.

## Anomaly rule (`app/anomaly.py`)
- Thresholds live **only** in `app/anomaly.py` as constants. Bounds are inclusive: a value on a bound is normal.
  - Temperature: 10–35 °C
  - Humidity: 20–80 %RH
  - Battery: ≥ 15 %
- Reason codes, always emitted in this order: `temperature_low | temperature_high`, `humidity_low | humidity_high`, `battery_low`. `is_anomaly = bool(reasons)`.
- Device status values are exactly `ok | anomaly | stale`, computed at query time by `compute_status()`, in the order stale > anomaly > ok.

## Simulator (`simulator/simulate.py`)
- Device IDs are `dev-001`…`dev-NNN`, from `f"dev-{index+1:03d}"`.
- The defaults are 20 devices, a 5 s interval per device and seed 42. That's round-robin with a tick every `interval/devices` seconds, about 4 requests/second.
- **The faulty devices are always the last 4**, in this order: `overheat, low_battery, invalid, dropout`. With the defaults they're dev-017…020. Every other device is `normal`.
- The profile constants (`OVERHEAT_STEP_C`, `LOW_BATTERY_*`, `INVALID_PROBABILITY`, `DROPOUT_PERIOD_S`) live at the top of `simulate.py`. Tests in `tests/test_simulator.py` check the anomaly timing, so update them together.
- Each device has its own `random.Random(seed + index)`, so runs are deterministic. Don't use the global `random` module.
- Each send uses `X-Request-ID: sim-<12 hex>`, set in `request_id_var` so the simulator's log line carries the same ID as the API's.
- Log events: `sim_startup`, `sim_send_failed` (WARNING, with `status_code` and, for dev-019, `fault=<invalid kind>`), `sim_summary` (with `stats`, every 30 s and on exit), and `sim_api_unavailable`.
- Compose service `simulator` reuses image `sensor-api:dev`, is in profile `sim` (off by default since A3), and sends through `envoy:8000`. It exits cleanly on SIGTERM/SIGINT and logs a final summary.

## Dashboard (`app/static/`)
- Plain HTML, CSS and JavaScript: `index.html`, `styles.css`, `app.js`. No framework, bundler, npm or CDN. `GET /` serves `index.html` (excluded from OpenAPI), and `/static` is a `StaticFiles` mount.
- The dashboard calls **only the public JSON API**. Don't add UI-specific endpoints.
- **Never duplicate anomaly thresholds in JavaScript.** Highlight values using the API's `anomaly_reasons`.
- Build the DOM with the `el()` helper and `textContent`. Never use `innerHTML` or `insertAdjacentHTML`; `tests/test_ui.py` checks this.
- UI requests send `X-Request-ID: ui-<12 hex>`. The request ID prefixes are `sim-` for the simulator, `ui-` for the dashboard, and a bare UUID for anything else.
- Colours are CSS variables on `:root`, redefined under `prefers-color-scheme: dark`. The layout stacks below 1180 px, and the page must not scroll horizontally at 375 px; only `.table-wrap` may scroll.
- The page refreshes every 5 s (`REFRESH_MS`). Detail charts show the last 50 readings (`HISTORY_LIMIT`).

## Metrics (`app/metrics.py`, Part B)
- The library is `prometheus_client`, using its **default registry** (which also provides `process_*` and `python_info`). Every metric object is defined in `app/metrics.py`; other modules import it and call `.inc()`, `.observe()` or `.set()`.
- `GET /metrics` serves the text format. It's excluded from the OpenAPI docs.
- **Naming:** `sensor_` prefix, snake_case, base units with a suffix (`_seconds`, `_total` for counters). Never use milliseconds in metrics.
- **Labels only take values from small fixed sets:** `method`, `route` (the same template the access log uses: `/devices/{device_id}`, `/static`, `unmatched`), `status_code`. **Never** use `device_id`, `request_id` or raw paths as labels; they belong in logs.
- HTTP metrics are recorded in `RequestContextMiddleware`, not in handlers.
- Latency histogram buckets are dense from 10 to 40 ms, where normal `POST /readings` latency sits. `histogram_quantile` interpolates inside a bucket, and coarse buckets overestimated p99 by 74 % (docs/BUILD_LOG.md, stage B7). If you change the buckets, re-run the logs-vs-histogram check, and keep bounds at 25 ms and 100 ms because the Apdex panel uses them (T and 4T).
- Business metrics are recorded where the event happens:
  - `sensor_readings_total{transport=http|grpc, outcome=normal|anomaly|replayed|conflict|rejected|failed}` (`app/ingest.py`)
  - `sensor_rate_limited_total{route}` (`/readings` or `SubmitReading`)
  - `sensor_grpc_requests_total{method,code}`, `sensor_grpc_request_duration_seconds{method}` (same `LATENCY_BUCKETS` as HTTP), `sensor_grpc_requests_in_progress{method}` (interceptor)
  - `sensor_reading_validation_errors_total{field,reason}`, where `field` goes through `metrics.validation_field()` so it's always one of the 5 fields or `body`
  - `sensor_anomalies_total{reason}`
  - `sensor_db_write_duration_seconds` (a Histogram since A2, wrapping `db.insert_reading`; two processes write)
  - `sensor_faults_injected_total{fault}` and `sensor_fault_active{fault}` (in `app/faults.py`)
- `sensor_devices{status}` comes from `DeviceStatusCollector`, which runs at scrape time using the same `compute_status()` as the API. `create_app()` calls `metrics.DEVICE_STATUS.configure(settings)`. Never keep a separate running device count.
- Create every known label combination at startup (see the loop in `metrics.py`), so series exist at 0.
- The image sets `PROMETHEUS_DISABLE_CREATED_SERIES=True`, so there are no `*_created` series.
- Tests: metrics accumulate in the process-wide registry, so assert on deltas (`after - before`) with `REGISTRY.get_sample_value`, never on absolute values.

## Logs pipeline (Part C)
- Images pinned to the same version: `elasticsearch:9.5.3`, `kibana:9.5.3`, `elastic/filebeat:9.5.3`, plus `curlimages/curl:8.16.0` for the setup job. Keep all three Elastic images on one version. No Logstash.
- Ports: Elasticsearch **127.0.0.1:9200**, Kibana **127.0.0.1:5601**. They're bound to localhost because security is off (`xpack.security.enabled=false`). Never publish them on 0.0.0.0.
- Elasticsearch is a single node with heap `-Xms512m -Xmx512m` and volume `es-data`. The whole stack needs about 3.5 GB of RAM (Kibana about 1.7 GB), and `docker compose stop kibana` doesn't stop ingestion.
- **Config lives in `monitoring/`:**
  - `filebeat/filebeat.yml`: Docker autodiscover on `container.name` regexp `^esd_hw3-(app|simulator)-[0-9]+$`, plus a second template for `^esd_hw3-envoy-[0-9]+$` (its stderr dropped by `drop_event`; **never** `stream: stdout` in the parser: Filebeat 9.5 then loses the offset on restart and re-ships the whole file). Paths are `*-json.log*` so Docker's rotated files are followed (the Compose project is pinned by `name: esd_hw3`). Label conditions don't match, because Filebeat nests the dotted label keys. The `container` parser unwraps Docker's envelope; the processors run `copy_fields` → `decode_json_fields` → `timestamp` → `drop_fields`.
  - `elasticsearch/index-template.json`: data stream `sensor-logs`, pattern `sensor-logs*`, 1 shard / 0 replicas, `dynamic: false` with explicit types.
  - `elasticsearch/ilm-policy.json`: `sensor-logs-policy`.
  - `kibana/saved-objects.ndjson`: data view id `sensor-logs` and the saved searches, with ids `sensor-<slug>`.
  - `logs-setup.sh`: run by the one-shot `logs-setup` service. It's idempotent; `filebeat` waits for it to finish (`service_completed_successfully`).
- **Index and field naming:** documents go to the data stream `sensor-logs` (backing indices `.ds-sensor-logs-<date>-00000N`). Field names are the app's own log fields at the top level (`event`, `level`, `request_id`, …), plus `container.{id,name,image.name}`, `host.name`, `stream`, and `log.original` (the raw line, stored but not indexed). When the app logs a **new field**, add it to `EXTRA_FIELDS` *and* to `index-template.json` (otherwise it's stored but not searchable), then re-run `docker compose up logs-setup`. The template only applies to the next backing index, i.e. from the next rollover.
- **Never add Docker labels or env vars to documents.** The `docker` object that autodiscover attaches includes the host's working directory (`/home/<user>/…`), so `drop_fields` must keep dropping `docker` and `container.labels`.
- **Retention (the ILM policy chosen):** roll over at `max_age: 10m` (or 1 GB); delete 1 h after rollover; the ILM poll interval is set to `1m` by `logs-setup`. So logs live for about 1 h 10 min. This is deliberately short for the demo; don't use "old" request IDs in docs or examples, because they get deleted.
- Docker log rotation for `app`, `envoy` and `simulator` uses the `x-app-logging` anchor: `json-file`, 3 × 10 MB.
- **Envoy access-log documents** use the app's field names (`service: envoy`, `event: envoy_access`, `request_id`, `status_code`, `grpc_code`, `duration_ms`, `replica` from `%RESP(X-REPLICA)%`) plus `listener`, `request_path`, `upstream_ms`, `upstream_host`, `upstream_host_name` (A/B/C), `upstream_cluster`, `response_flags`, all in `index-template.json`.
- Elasticsearch disk watermarks are 93/95/97 % (set by `logs-setup.sh`, A3-17), because the host disk is nearly full.
- `docker compose up` of `filebeat` re-runs `logs-setup`, which waits for Kibana; with Kibana stopped, use `docker compose restart filebeat`.
- Checking what's stored: `exists` / `query_string` only see *indexed* fields. To prove a value is absent, scan `_source` (see docs/BUILD_LOG.md C6).
- Kibana: env settings must be on the Docker image's allowed list. The banner is hidden with `TELEMETRY_OPTIN=false` plus `TELEMETRY_ALLOWCHANGINGOPTINSTATUS=false`.

## A3 experiments (`docs/results/a3/`, screenshots `docs/screenshots/a3/`)
- Test traffic is read-only: HTTP `GET /devices/{device_id}`, gRPC `GetDevice` (A3-1). Devices dev-001…020 are created by `scripts/lb_load.py --seed`.
- One run is `scripts/lb_run.sh <phase> <name>`: `lb_load.py` at 50 req/s × 120 s per interface (open loop; HTTP a connection per request, gRPC one channel; request ids `lb-<run>-<iface>-NNNNN`; no retries), then `lb_run_stats.py`.
  - Per-run counts are summed from **raw samples** (handling counter resets), never `increase()`, which extrapolates (A3-15).
  - Mean in flight is Little's law (`_sum` increase / seconds), because the HTTP in-progress gauge counts the `/metrics` scrape itself.
- Faults: `scripts/replica_fault.sh <A|B|C> delay <ms> | error <n> | off` (docker exec, never through Envoy). `FaultState` applies to `POST /readings`, `GET /devices/{device_id}` and `GetDevice`.
- Failure: `scripts/lb_failure.py --interface http|grpc` (stop B at 30 s, start at 70 s, `/clusters` polled every 250 ms).
- Logs: `scripts/lb_es_check.py <run.json>` (distinct request ids per replica: exact `terms`, not `cardinality`) and `scripts/lb_log_pair.py <request_id>`. Run them within about an hour of the run, because of retention.
- Predictions in `docs/results/a3/d_predictions.md` are written before the runs and never edited. The table: `scripts/lb_summary.py` → `summary.md`.
- RAM is tight (7.7 GB): stop Kibana during measured runs; for Kibana screenshots, stop app/envoy/prometheus/grafana first.

## Experiments (A1 Part E)
- `scripts/load.py` and `scripts/e1_stage_stats.py` use the **standard library only**, so they run with the system `python3`. The load is **open loop** (a fixed start rate, with a thread pool), and each request ID is `load-<stage>-NNNNN`. Stop the simulator during E1, so the fault's every-Nth counter and the percentiles see only the load.
- Per-stage server numbers come from `increase(...[stage length − 15 s])` evaluated at the stage end (see `e1_stage_stats.py`), never from a `[1m]` panel value read at some moment.
- **The E2 cardinality demo must never touch the app's registry or labels.** `scripts/cardinality_demo.py` builds its own `CollectorRegistry()` in `build_registry()` and caps IDs at `MAX_IDS = 100`; `tests/test_cardinality_demo.py` enforces both. It runs only as the Compose service `cardinality-demo` in profile **`e2`**, never in the default stack.
- Prometheus finds the demo through the job `cardinality-demo`, which uses `file_sd_configs` on `monitoring/prometheus/file_sd/*.json`. **`cardinality-demo.json` must be `[]` in git**, and the demo script writes the target and resets it (a `trap` on exit). Never add a `request_id`-style label to anything in `app/`.

## Code rules
- All output goes through `logging`. Never use `print()`.
- Timestamps always use `app/timeutil.py`: `utc_iso()` produces ISO-8601 UTC with ms precision and a `Z` suffix, e.g. `2026-09-26T10:00:00.000Z`.

## Logging conventions
- Call `setup_logging(service)` once, at the entry point only (`app/__main__.py`, and the simulator). Never call it in `create_app()`, because tests rely on pytest's `caplog`.
- One JSON object per line on stdout. Every line has these base fields: `timestamp, level, service, logger, event, message, request_id`, plus `replica` (A/B/C) in the app since A3 (`logging_setup.set_replica()`, called in `app/__main__.py`).
- Log with `log.<level>("human message", extra={"event": "<snake_case>", ...})`. Every app log line needs an `event`; uvicorn's lines get `event: "log"`.
- Extra fields are only emitted if they're listed in `EXTRA_FIELDS` in `app/logging_setup.py`. To log a new field, add it there. Never log request bodies, client IPs or secrets.
- `service` is `sensor-api` for the API and `simulator` for the simulator.
- `X-Request-ID`: accepted when it matches `^[A-Za-z0-9._-]{1,64}$`, otherwise replaced with `uuid4().hex`. It's echoed on every response and stored in `request_id_var`.
- `http_request.path` is the route template (e.g. `/devices/{device_id}`), the mount prefix for mounted apps (`/static`), or `unmatched` when nothing matched. Never use the raw URL, so the set of values stays bounded.
- Crashes are caught in `RequestContextMiddleware`, which returns a JSON 500 and logs `event=unhandled_exception` with a `stack` field.
